"""In-Fusion self-test. Runs only when the SEQ_BRIDGE_SELFTEST environment variable
names an output folder. Builds sample parts in a new document, runs the bridge build on
each, and compares the volume change with the value worked out by hand."""
import json
import math
import os
import traceback

import adsk.core
import adsk.fusion

BLOCK = 3.0      # cm
HEIGHT = 1.0
POCKET_DEPTH = 0.4
HOLE_R = 0.3
RECT = (1.2, 1.0)   # pocket size; bridges should span the 1.0 direction


def _poly_area(level, r):
    sides = 2 ** level
    return sides * r * r * math.tan(math.pi / sides)


def _expected_rect(bridges, layer, add_mode):
    """Volume change for the rectangular pocket with a centred hole."""
    hole = math.pi * HOLE_R ** 2
    foot = RECT[0] * RECT[1]
    total = 0.0
    for k in range(1, bridges + 1):
        opening = 2 * HOLE_R * min(RECT) if k == 1 else _poly_area(k, HOLE_R)
        total += (foot - opening) * layer if add_mode else -(opening - hole) * layer
    return total


def _closed_sketch(comp, pts):
    sk = comp.sketches.add(comp.xYConstructionPlane)
    lines = sk.sketchCurves.sketchLines
    p = [adsk.core.Point3D.create(x, y, 0) for x, y in pts]
    first = lines.addByTwoPoints(p[0], p[1])
    prev = first
    for q in p[2:]:
        prev = lines.addByTwoPoints(prev.endSketchPoint, q)
    lines.addByTwoPoints(prev.endSketchPoint, first.startSketchPoint)
    return sk


def _make_part(comp, x0, pocket_pts):
    """Block with a pocket cut from the bottom and a through hole. Returns (body, ceiling face)."""
    ext = comp.features.extrudeFeatures
    ops = adsk.fusion.FeatureOperations
    v = adsk.core.ValueInput.createByReal
    cx, cy = x0 + BLOCK / 2, BLOCK / 2

    sk = _closed_sketch(comp, [(x0, 0), (x0 + BLOCK, 0), (x0 + BLOCK, BLOCK), (x0, BLOCK)])
    body = ext.addSimple(sk.profiles.item(0), v(HEIGHT), ops.NewBodyFeatureOperation).bodies.item(0)

    sk = _closed_sketch(comp, [(cx + x, cy + y) for x, y in pocket_pts])
    ext.addSimple(sk.profiles.item(0), v(POCKET_DEPTH), ops.CutFeatureOperation)

    sk = comp.sketches.add(comp.xYConstructionPlane)
    sk.sketchCurves.sketchCircles.addByCenterRadius(adsk.core.Point3D.create(cx, cy, 0), HOLE_R)
    ext.addSimple(sk.profiles.item(0), v(HEIGHT), ops.CutFeatureOperation)

    for f in body.faces:
        if (f.geometry.surfaceType == adsk.core.SurfaceTypes.PlaneSurfaceType and f.loops.count == 2
                and abs(f.pointOnFace.z - POCKET_DEPTH) < 1e-6):
            return body, f
    raise RuntimeError('test part has no pocket ceiling face')


def run_selftest(out_dir, build):
    app = adsk.core.Application.get()
    report = {'version': app.version, 'cases': []}
    try:
        app.documents.add(adsk.core.DocumentTypes.FusionDesignDocumentType)
        design = adsk.fusion.Design.cast(app.activeProduct)
        root = design.rootComponent

        w, d = RECT[0] / 2, RECT[1] / 2
        rect = [(-w, -d), (w, -d), (w, d), (-w, d)]
        hex_r = 0.5 / math.cos(math.pi / 6)   # 10 mm across flats
        rot = math.radians(17)
        hexagon = [(hex_r * math.cos(rot + i * math.pi / 3), hex_r * math.sin(rot + i * math.pi / 3)) for i in range(6)]

        cases = [
            ('rect cut 2', rect, 2, 0.02, False),
            ('rect add 2', rect, 2, 0.02, True),
            ('rect cut 3', rect, 3, 0.02, False),
            ('rect add 3', rect, 3, 0.02, True),
            ('rect cut 1', rect, 1, 0.03, False),
            ('rect add 1', rect, 1, 0.03, True),
            ('hex cut 2', hexagon, 2, 0.02, False),
            ('hex add 3', hexagon, 3, 0.02, True),
        ]
        for i, (name, outline, bridges, layer, add_mode) in enumerate(cases):
            case = {'name': name}
            try:
                body, face = _make_part(root, i * (BLOCK + 1.0), outline)
                before = body.volume
                n_bodies = root.bRepBodies.count
                build(face, bridges, layer, add_mode, 0.0)
                case['delta'] = body.volume - before
                case['bodies_unchanged'] = root.bRepBodies.count == n_bodies
                if outline is rect:
                    case['expected'] = _expected_rect(bridges, layer, add_mode)
                    case['ok'] = case['bodies_unchanged'] and abs(case['delta'] - case['expected']) < 1e-6
                else:
                    case['ok'] = case['bodies_unchanged'] and (case['delta'] > 0) == add_mode and abs(case['delta']) > 1e-5
            except Exception:
                case['ok'] = False
                case['error'] = traceback.format_exc()
            report['cases'].append(case)

        try:
            vp = app.activeViewport
            for i, (name, _, _, _, _) in enumerate(cases):
                cx, cy = i * (BLOCK + 1.0) + BLOCK / 2, BLOCK / 2
                cam = vp.camera
                cam.isSmoothTransition = False
                cam.isFitView = False
                cam.target = adsk.core.Point3D.create(cx, cy, POCKET_DEPTH)
                cam.eye = adsk.core.Point3D.create(cx - 1.0, cy - 1.6, POCKET_DEPTH - 3.0)
                cam.upVector = adsk.core.Vector3D.create(0, 1, 0)
                cam.viewExtents = 1.1
                vp.camera = cam
                vp.refresh()
                adsk.doEvents()
                vp.saveAsImageFile(os.path.join(out_dir, name.replace(' ', '_') + '.png'), 900, 700)
        except Exception:
            report['image_error'] = traceback.format_exc()
    except Exception:
        report['error'] = traceback.format_exc()
    with open(os.path.join(out_dir, 'selftest_result.json'), 'w') as f:
        json.dump(report, f, indent=1)
