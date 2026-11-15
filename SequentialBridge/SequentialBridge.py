"""Sequential Bridge - Fusion add-in.

Pick the flat face that has a hole in it (the "ceiling" of a pocket when the part is
printed pocket-down). The add-in builds sequential bridge layers so the hole prints
without supports: layer 1 leaves a slot, layer 2 a square, layer 3 an octagon, ...

Cut: the layers are cut into the solid behind the face (pocket depth is unchanged).
Add: the layers are added in front of the face (pocket gets shallower).
"""
import math
import os
import tempfile
import traceback

import adsk.core
import adsk.fusion

from . import bridge_geometry as geo

CMD_ID = 'sequentialBridgeCmd'
CMD_NAME = 'Sequential Bridge'
CMD_TOOLTIP = 'Add sequential bridging layers to a face with a hole so it 3D prints without supports.'
WORKSPACE_ID = 'FusionSolidEnvironment'
PANEL_ID = 'SolidModifyPanel'

MAX_BRIDGES = 5
STROKE_TOLERANCE = 0.001  # cm

_handlers = []


# ---------------------------------------------------------------- small vector helpers

def _t(v):
    return (v.x, v.y, v.z)


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _unit(a):
    length = math.sqrt(_dot(a, a))
    return (a[0] / length, a[1] / length, a[2] / length)


class _Frame:
    """2D coordinate frame lying in the face plane; +normal points out of the solid."""

    def __init__(self, origin, normal, x_hint):
        self.o = origin
        self.n = _unit(normal)
        # make x exactly perpendicular to the normal
        d = _dot(x_hint, self.n)
        self.ex = _unit(_sub(x_hint, (self.n[0] * d, self.n[1] * d, self.n[2] * d)))
        self.ey = _cross(self.n, self.ex)

    def to2d(self, p):
        d = _sub(_t(p), self.o)
        return (_dot(d, self.ex), _dot(d, self.ey))

    def to3d(self, x, y, off=0.0):
        return adsk.core.Point3D.create(
            self.o[0] + x * self.ex[0] + y * self.ey[0] + off * self.n[0],
            self.o[1] + x * self.ex[1] + y * self.ey[1] + off * self.n[1],
            self.o[2] + x * self.ex[2] + y * self.ey[2] + off * self.n[2],
        )

    def height(self, p):
        return _dot(_sub(_t(p), self.o), self.n)


# ---------------------------------------------------------------- reading the face

def _is_planar(face):
    return face.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType


def _find_holes(face):
    """Circular inner loops of the face as (center Point3D, radius)."""
    circ = (adsk.core.Curve3DTypes.Circle3DCurveType, adsk.core.Curve3DTypes.Arc3DCurveType)
    holes = []
    for loop in face.loops:
        if loop.isOuter:
            continue
        center, radius, ok = None, None, True
        for edge in loop.edges:
            g = edge.geometry
            if g.curveType not in circ:
                ok = False
                break
            if center is None:
                center, radius = g.center, g.radius
            elif center.distanceTo(g.center) > 1e-6 or abs(radius - g.radius) > 1e-6:
                ok = False
                break
        if ok and center is not None:
            holes.append((center, radius))
    return holes


def _outer_loop(face):
    for loop in face.loops:
        if loop.isOuter:
            return loop
    raise RuntimeError('The face has no outer boundary.')


def _outer_boundary_2d(face, frame):
    """Outer boundary as 2D segments, plus the directions of its straight edges."""
    segments, angles = [], []
    for edge in _outer_loop(face).edges:
        ev = edge.evaluator
        ok, t0, t1 = ev.getParameterExtents()
        if not ok:
            continue
        ok, pts = ev.getStrokes(t0, t1, STROKE_TOLERANCE)
        if not ok:
            continue
        pts2 = [frame.to2d(p) for p in pts]
        for a, b in zip(pts2[:-1], pts2[1:]):
            segments.append((a, b))
        if edge.geometry.curveType == adsk.core.Curve3DTypes.Line3DCurveType and len(pts2) >= 2:
            a, b = pts2[0], pts2[-1]
            angles.append(math.atan2(b[1] - a[1], b[0] - a[0]))
    return segments, angles


# ---------------------------------------------------------------- feature helpers

def _all_profiles(sketch):
    if sketch.profiles.count == 0:
        raise RuntimeError('Sketch "%s" produced no closed profile.' % sketch.name)
    coll = adsk.core.ObjectCollection.create()
    for p in sketch.profiles:
        coll.add(p)
    return coll


def _draw_closed(sketch, points3d):
    """Draw a closed polyline through model-space points."""
    pts = []
    for p in points3d:
        q = sketch.modelToSketchSpace(p)
        pts.append(adsk.core.Point3D.create(q.x, q.y, 0))
    lines = sketch.sketchCurves.sketchLines
    first = lines.addByTwoPoints(pts[0], pts[1])
    prev = first
    for p in pts[2:]:
        prev = lines.addByTwoPoints(prev.endSketchPoint, p)
    lines.addByTwoPoints(prev.endSketchPoint, first.startSketchPoint)


def _sketch_points_along_normal(sketch, frame):
    z = sketch.xDirection.crossProduct(sketch.yDirection)
    return _dot(_t(z), frame.n) > 0


def _extrude_new_body(comp, sketch, dist, test_point, frame, toward_normal):
    """Extrude all profiles of `sketch` as a new body on the wanted side of the plane.

    The direction is guessed from the sketch orientation and then verified with
    `test_point`; if the body landed on the wrong side it is redone the other way.
    """
    extrudes = comp.features.extrudeFeatures
    pos = adsk.fusion.ExtentDirections.PositiveExtentDirection
    neg = adsk.fusion.ExtentDirections.NegativeExtentDirection
    guess_pos = _sketch_points_along_normal(sketch, frame) == toward_normal
    inside = adsk.fusion.PointContainment.PointInsidePointContainment
    for direction in ((pos, neg) if guess_pos else (neg, pos)):
        inp = extrudes.createInput(_all_profiles(sketch), adsk.fusion.FeatureOperations.NewBodyFeatureOperation)
        extent = adsk.fusion.DistanceExtentDefinition.create(adsk.core.ValueInput.createByReal(dist))
        inp.setOneSideExtent(extent, direction)
        ext = extrudes.add(inp)
        if ext.bodies.count and ext.bodies.item(0).pointContainment(test_point) == inside:
            return ext
        ext.deleteMe()
    raise RuntimeError('Could not extrude the face outline on the expected side.')


def _symmetric_extrude(comp, sketch, half_dist, operation, participants):
    extrudes = comp.features.extrudeFeatures
    inp = extrudes.createInput(_all_profiles(sketch), operation)
    inp.setSymmetricExtent(adsk.core.ValueInput.createByReal(half_dist), False)
    inp.participantBodies = list(participants)
    return extrudes.add(inp)


def _new_bodies(comp, before):
    return [b for b in comp.bRepBodies if not any(b == old for old in before)]


def _find_cap_face(body, frame, height):
    """Planar face of `body` parallel to the selected face at the given height."""
    for f in body.faces:
        if not _is_planar(f):
            continue
        ok, nrm = f.evaluator.getNormalAtPoint(f.pointOnFace)
        if not ok or abs(abs(_dot(_t(nrm), frame.n)) - 1.0) > 1e-6:
            continue
        if abs(frame.height(f.pointOnFace) - height) < 1e-6:
            return f
    raise RuntimeError('Could not find the end face of the added layers.')


# ---------------------------------------------------------------- the build

def build(face, bridges, layer, add_mode, rotate):
    """Create the sequential bridge features. Lengths in cm, angle in radians."""
    app = adsk.core.Application.get()
    design = adsk.fusion.Design.cast(app.activeProduct)

    if face.assemblyContext:
        face = face.nativeObject
    if not _is_planar(face):
        raise RuntimeError('Select a flat face.')
    holes3d = _find_holes(face)
    if not holes3d:
        raise RuntimeError('The selected face has no round hole in it.')
    if layer <= 0:
        raise RuntimeError('Layer height must be greater than zero.')
    bridges = max(1, min(MAX_BRIDGES, int(bridges)))

    body = face.body
    comp = body.parentComponent

    anchor = face.pointOnFace
    ok, normal = face.evaluator.getNormalAtPoint(anchor)
    if not ok:
        raise RuntimeError('Could not read the face direction.')
    frame = _Frame(_t(anchor), _t(normal), _t(face.geometry.uDirection))

    segments, edge_angles = _outer_boundary_2d(face, frame)
    bb = face.boundingBox
    max_r = max(r for _, r in holes3d)
    half_len = bb.minPoint.distanceTo(bb.maxPoint) + 2.0 * max_r

    holes = []
    for center, r in holes3d:
        cx, cy = frame.to2d(center)
        theta = geo.choose_theta(cx, cy, r, segments, edge_angles) + rotate
        holes.append((cx, cy, r, theta))

    parametric = design.designType == adsk.fusion.DesignTypes.ParametricDesignType
    timeline_start = design.timeline.markerPosition if parametric else 0
    before = [b for b in comp.bRepBodies]
    sketches = []
    total = bridges * layer

    # Outline of the face without its holes: this is the pocket footprint.
    foot = comp.sketches.addWithoutEdges(face)
    foot.name = 'Bridge footprint'
    for edge in _outer_loop(face).edges:
        foot.project(edge)
    sketches.append(foot)

    def opening_sketch(plane_face, level, off):
        sk = comp.sketches.addWithoutEdges(plane_face)
        sk.name = 'Bridge layer %d' % level
        sk.isComputeDeferred = True
        for cx, cy, r, theta in holes:
            outline = geo.opening_outline(cx, cy, r, level, theta, half_len)
            _draw_closed(sk, [frame.to3d(x, y, off) for x, y in outline])
        sk.isComputeDeferred = False
        sketches.append(sk)
        return sk

    cut_op = adsk.fusion.FeatureOperations.CutFeatureOperation
    intersect_op = adsk.fusion.FeatureOperations.IntersectFeatureOperation
    join_op = adsk.fusion.FeatureOperations.JoinFeatureOperation

    if add_mode:
        # Fill the pocket by the full stack height, then open each layer from the far side.
        ext = _extrude_new_body(comp, foot, total, frame.to3d(0, 0, total / 2.0), frame, True)
        cap = _find_cap_face(ext.bodies.item(0), frame, total)
        level_sketches = [opening_sketch(cap, k, total) for k in range(1, bridges + 1)]
        for k, sk in enumerate(level_sketches, start=1):
            # Symmetric about the far plane: only the fill body takes part, and there is
            # nothing of it beyond that plane, so the cut is k layers deep.
            _symmetric_extrude(comp, sk, k * layer, cut_op, _new_bodies(comp, before))
        final_op = join_op
    else:
        # One tool body per layer: footprint prism k layers deep, trimmed to that layer's opening.
        level_sketches = [opening_sketch(face, k, 0.0) for k in range(1, bridges + 1)]
        for k, sk in enumerate(level_sketches, start=1):
            depth = k * layer
            ext = _extrude_new_body(comp, foot, depth, frame.to3d(0, 0, -depth / 2.0), frame, False)
            _symmetric_extrude(comp, sk, depth + layer, intersect_op, [ext.bodies.item(0)])
        final_op = cut_op

    tools = adsk.core.ObjectCollection.create()
    for b in _new_bodies(comp, before):
        tools.add(b)
    if tools.count == 0:
        raise RuntimeError('No bridge geometry was produced.')
    combines = comp.features.combineFeatures
    cinp = combines.createInput(body, tools)
    cinp.operation = final_op
    cinp.isKeepToolBodies = False
    combines.add(cinp)

    for sk in sketches:
        try:
            sk.isVisible = False
        except Exception:
            pass

    if parametric:
        try:
            end = design.timeline.markerPosition - 1
            if end > timeline_start:
                group = design.timeline.timelineGroups.add(timeline_start, end)
                group.name = CMD_NAME
        except Exception:
            pass


# ---------------------------------------------------------------- command plumbing

def _read_inputs(inputs):
    sel = adsk.core.SelectionCommandInput.cast(inputs.itemById('face'))
    face = adsk.fusion.BRepFace.cast(sel.selection(0).entity) if sel.selectionCount else None
    bridges = adsk.core.IntegerSpinnerCommandInput.cast(inputs.itemById('bridges')).value
    layer = adsk.core.ValueCommandInput.cast(inputs.itemById('layer')).value
    op = adsk.core.DropDownCommandInput.cast(inputs.itemById('op')).selectedItem.name
    rotate = adsk.core.ValueCommandInput.cast(inputs.itemById('rotate')).value
    return face, bridges, layer, op == 'Add', rotate


class _CreatedHandler(adsk.core.CommandCreatedEventHandler):
    def notify(self, args):
        try:
            cmd = args.command
            inputs = cmd.commandInputs

            sel = inputs.addSelectionInput('face', 'Face', 'Select the flat face that has the hole in it')
            sel.addSelectionFilter('PlanarFaces')
            sel.setSelectionLimits(1, 1)

            inputs.addIntegerSpinnerCommandInput('bridges', 'Bridges', 1, MAX_BRIDGES, 1, 2)
            inputs.addValueInput('layer', 'Layer height', 'mm', adsk.core.ValueInput.createByString('0.2 mm'))

            op = inputs.addDropDownCommandInput('op', 'Operation', adsk.core.DropDownStyles.TextListDropDownStyle)
            op.listItems.add('Cut', True)
            op.listItems.add('Add', False)

            inputs.addValueInput('rotate', 'Rotate bridges', 'deg', adsk.core.ValueInput.createByString('0 deg'))

            for handler_cls, event in (
                (_ExecuteHandler, cmd.execute),
                (_PreviewHandler, cmd.executePreview),
                (_ValidateHandler, cmd.validateInputs),
                (_SelectionHandler, cmd.preSelect),
            ):
                h = handler_cls()
                event.add(h)
                _handlers.append(h)
        except Exception:
            adsk.core.Application.get().userInterface.messageBox(traceback.format_exc())


def _log(msg):
    try:
        with open(os.path.join(tempfile.gettempdir(), 'SequentialBridge.log'), 'a') as f:
            f.write(msg.rstrip() + '\n')
    except Exception:
        pass


class _ValidateHandler(adsk.core.ValidateInputsEventHandler):
    def notify(self, args):
        # Only checks that do not look at the model: while a preview is showing, the
        # selected face has already been changed by it.
        try:
            inputs = args.inputs
            sel = adsk.core.SelectionCommandInput.cast(inputs.itemById('face'))
            layer = adsk.core.ValueCommandInput.cast(inputs.itemById('layer'))
            rotate = adsk.core.ValueCommandInput.cast(inputs.itemById('rotate'))
            args.areInputsValid = (sel.selectionCount == 1 and layer.isValidExpression
                                   and layer.value > 0 and rotate.isValidExpression)
        except Exception:
            _log('validate: ' + traceback.format_exc())
            args.areInputsValid = False


class _SelectionHandler(adsk.core.SelectionEventHandler):
    """Only let faces with a round hole be picked."""

    def notify(self, args):
        try:
            face = adsk.fusion.BRepFace.cast(args.selection.entity)
            if face is None:
                args.isSelectable = False
                return
            native = face.nativeObject if face.assemblyContext else face
            args.isSelectable = _is_planar(native) and bool(_find_holes(native))
        except Exception:
            args.isSelectable = True


class _PreviewHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            face, bridges, layer, add_mode, rotate = _read_inputs(args.command.commandInputs)
            if face is None:
                return
            build(face, bridges, layer, add_mode, rotate)
        except Exception:
            # A failed preview is not fatal; OK runs the build again and reports the error.
            _log('preview: ' + traceback.format_exc())
        # The preview is never kept as the result: OK always rebuilds from the clean model.
        args.isValidResult = False


class _ExecuteHandler(adsk.core.CommandEventHandler):
    def notify(self, args):
        try:
            face, bridges, layer, add_mode, rotate = _read_inputs(args.command.commandInputs)
            build(face, bridges, layer, add_mode, rotate)
        except Exception:
            _log('execute: ' + traceback.format_exc())
            args.executeFailed = True
            args.executeFailedMessage = 'Sequential Bridge failed:<br>' + traceback.format_exc().replace('\n', '<br>')


class _StartupHandler(adsk.core.ApplicationEventHandler):
    def __init__(self, out_dir):
        super().__init__()
        self.out_dir = out_dir

    def notify(self, args):
        from . import selftest
        selftest.run_selftest(self.out_dir, build)


def _maybe_selftest():
    """Developer hook: run the in-Fusion self-test when SEQ_BRIDGE_SELFTEST names a folder.

    Only fires when the add-in loads during Fusion startup, so stopping and starting the
    add-in by hand never creates test documents.
    """
    out_dir = os.environ.get('SEQ_BRIDGE_SELFTEST')
    app = adsk.core.Application.get()
    if not out_dir or app.isStartupComplete:
        return
    h = _StartupHandler(out_dir)
    app.startupCompleted.add(h)
    _handlers.append(h)


def run(context):
    try:
        ui = adsk.core.Application.get().userInterface
        old = ui.commandDefinitions.itemById(CMD_ID)
        if old:
            old.deleteMe()
        resources = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'resources')
        cmd_def = ui.commandDefinitions.addButtonDefinition(CMD_ID, CMD_NAME, CMD_TOOLTIP, resources)
        created = _CreatedHandler()
        cmd_def.commandCreated.add(created)
        _handlers.append(created)

        panel = ui.allToolbarPanels.itemById(PANEL_ID)
        if panel and not panel.controls.itemById(CMD_ID):
            panel.controls.addCommand(cmd_def)
        _maybe_selftest()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(traceback.format_exc())


def stop(context):
    try:
        ui = adsk.core.Application.get().userInterface
        panel = ui.allToolbarPanels.itemById(PANEL_ID)
        if panel:
            ctrl = panel.controls.itemById(CMD_ID)
            if ctrl:
                ctrl.deleteMe()
        cmd_def = ui.commandDefinitions.itemById(CMD_ID)
        if cmd_def:
            cmd_def.deleteMe()
        _handlers.clear()
    except Exception:
        adsk.core.Application.get().userInterface.messageBox(traceback.format_exc())
