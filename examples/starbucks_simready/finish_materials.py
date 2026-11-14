"""Case-specific, explicitly estimated material completion for the cafe.

Preserve source observations and provenance. Missing illustration pixels use a
photographic exemplar from the same cafe; this is not exact artwork recovery.
Wood is a clean observed material sample, not a photo of cups baked into a table.
"""

import json

import cv2
import numpy as np
from pxr import Gf, Usd, UsdGeom, UsdShade, Vt

from simready.core.artifacts import digest, write_json


def finish_materials(directory, record):
    if record.get("material_completion_revision") == 4:
        return record
    textures = directory / "appearance_textures"
    previous = bool(record.get("material_completion_revision"))
    wood_source = cv2.imread(
        str(textures / ("Table01_before_completion.png" if previous else "Table01.png"))
    )
    clean_wood = cv2.resize(
        wood_source[390:620, 200:540], (768, 768), interpolation=cv2.INTER_CUBIC
    )
    exemplar = cv2.imread(
        str(
            textures
            / ("EastMural_before_completion.png" if previous else "EastMural.png")
        )
    )
    stage = Usd.Stage.Open(str(directory / "appearance_reference.usda"))
    completions = []
    for asset in record["assets"]:
        name = asset["id"].split("/")[-1]
        if name == "SouthPoster":
            continue
        path = textures / (name + ".png")
        if not path.exists():
            continue
        original = cv2.imread(
            str(textures / (name + "_before_completion.png") if previous else path)
        )
        cv2.imwrite(str(textures / (name + "_before_completion.png")), original)
        observed_path = textures / (name + "_observed.png")
        observed = cv2.imread(str(observed_path), 0) > 0
        h, w = observed.shape
        if asset["category"] == "table":
            output = clean_wood.copy()
            estimated = np.ones((h, w), bool)
            method = "clean observed wood material sample transferred over the modeled tabletop; does not bake photographed objects into support geometry"
        elif asset["category"] == "wall":
            estimated = ~observed
            person = np.zeros((h, w), np.uint8)
            if name == "NorthMural3":
                mask_path = (
                    directory / "appearance_textures/NorthMural3_transient_mask.png"
                )
                if mask_path.exists():
                    person = cv2.imread(str(mask_path), 0)
                    if person.shape != (h, w):
                        raise ValueError("Transient mask must match its source atlas")
                    # Grounded SAM also selects a person-shaped illustration.
                    # Restrict it to the known lower-wall transient height band.
                    person[: int(h * 0.80)] = 0
                else:
                    polygon = np.array(
                        [
                            [0.12, 0.82],
                            [0.28, 0.82],
                            [0.34, 0.90],
                            [0.38, 1],
                            [0, 1],
                            [0.05, 0.90],
                        ]
                    ) * [w, h]
                    cv2.fillPoly(person, [polygon.astype(np.int32)], 255)
                person[int(h * 0.95) :, : int(w * 0.38)] = 255
                person = cv2.dilate(person, np.ones((41, 41), np.uint8))
                estimated |= person > 0
            fallback = cv2.resize(exemplar, (w, h), interpolation=cv2.INTER_CUBIC)
            if name == "NorthMural3":
                fallback = np.roll(original, int(w * 0.47), axis=1)
                fallback[: int(h * 0.8)] = cv2.resize(exemplar, (w, h))[: int(h * 0.8)]
                generated_path = textures / "NorthMural3_generated.png"
                if generated_path.exists():
                    generated = cv2.resize(cv2.imread(str(generated_path)), (w, h))
                    # Register the tool output to unchanged observed motifs.
                    sift = cv2.SIFT_create(nfeatures=6000)
                    valid = (observed & (person == 0)).astype(np.uint8) * 255
                    ka, da = sift.detectAndCompute(
                        cv2.cvtColor(generated, cv2.COLOR_BGR2GRAY), valid
                    )
                    kb, db = sift.detectAndCompute(
                        cv2.cvtColor(original, cv2.COLOR_BGR2GRAY), valid
                    )
                    matches = cv2.BFMatcher().knnMatch(da, db, k=2)
                    good = [a for a, b in matches if a.distance < 0.72 * b.distance]
                    if len(good) < 20:
                        raise ValueError(
                            "Generated mural cannot be registered to source motifs"
                        )
                    matrix, inliers = cv2.findHomography(
                        np.float32([ka[m.queryIdx].pt for m in good]),
                        np.float32([kb[m.trainIdx].pt for m in good]),
                        cv2.USAC_MAGSAC,
                        3.0,
                    )
                    if matrix is None or np.count_nonzero(inliers) < 20:
                        raise ValueError("Generated mural registration failed")
                    generated = cv2.warpPerspective(
                        generated, matrix, (w, h), borderMode=cv2.BORDER_REPLICATE
                    )
                    # Match brightness using the unchanged lower-wall samples.
                    ring = (
                        (cv2.dilate(person, np.ones((101, 101), np.uint8)) > 0)
                        & (person == 0)
                        & observed
                    )
                    delta = np.median(
                        original[ring].astype(float) - generated[ring], axis=0
                    )
                    generated = np.uint8(
                        np.clip(generated.astype(float) + delta, 0, 255)
                    )
                    fallback[person > 0] = generated[person > 0]
                    write_json(
                        textures / "NorthMural3_generated_registration.json",
                        {
                            "homography": matrix.tolist(),
                            "inliers": int(np.count_nonzero(inliers)),
                            "generated_sha256": digest(generated_path),
                            "scope": "generated pixels restricted to transient occlusion mask; not observed truth",
                        },
                    )
            if name == "Entry":
                # Tiny unsupported door-border samples: local continuation,
                # never transfer the mural illustration onto the entrance.
                fallback = cv2.inpaint(
                    original, estimated.astype(np.uint8) * 255, 5, cv2.INPAINT_TELEA
                )
            alpha = (
                1
                - np.clip(
                    cv2.distanceTransform(estimated.astype(np.uint8), cv2.DIST_L2, 3)
                    / 8,
                    0,
                    1,
                )[..., None]
            )
            output = np.uint8(
                np.clip(original * alpha + fallback * (1 - alpha), 0, 255)
            )
            if (
                name == "NorthMural3"
                and not (textures / "NorthMural3_generated.png").exists()
            ):
                # Keep the mural's lower border continuous through the former
                # person silhouette instead of leaving a rectangular pasted edge.
                local = person.copy()
                local[:3] = local[-3:] = 0
                local[:, :3] = local[:, -3:] = 0
                x, y, bw, bh = cv2.boundingRect(local)
                cloned = cv2.seamlessClone(
                    fallback,
                    original,
                    local,
                    (x + bw // 2, y + bh // 2),
                    cv2.NORMAL_CLONE,
                )
                output[local > 0] = cloned[local > 0]
            method = "same-cafe photographic exemplar continuation only in unsupported or transient-occluded texels; exact illustration behind occlusion is unverified"
        else:
            continue
        cv2.imwrite(str(path), output)
        cv2.imwrite(
            str(textures / (name + "_estimated.png")), estimated.astype(np.uint8) * 255
        )
        observed &= ~estimated
        cv2.imwrite(str(observed_path), observed.astype(np.uint8) * 255)
        owner_path = textures / (name + "_source_index.npy")
        owner = np.load(owner_path, allow_pickle=False)
        owner[estimated] = -1
        np.save(owner_path, owner)
        meta_path = path.with_suffix(".json")
        meta = json.loads(meta_path.read_text())
        meta.update(
            texture_sha256=digest(path),
            observed_fraction=float(observed.mean()),
            estimated_fraction=float(1 - observed.mean()),
            material_completion={
                "method": method,
                "estimated_mask_sha256": digest(textures / (name + "_estimated.png")),
                "before_sha256": digest(textures / (name + "_before_completion.png")),
            },
        )
        write_json(meta_path, meta)
        asset["texture"] = meta
        completions.append(
            {
                "id": name,
                "method": method,
                "estimated_fraction": float(estimated.mean()),
            }
        )
    for name in ["EastMural", "Entry", "EastBase"]:
        mesh = UsdGeom.Mesh(stage.GetPrimAtPath("/Appearance/Walls/" + name))
        mesh.GetFaceVertexIndicesAttr().Set([3, 2, 1, 0])
        pts = np.asarray(mesh.GetPointsAttr().Get())
        normal = np.cross(pts[2] - pts[3], pts[1] - pts[3])
        normal /= np.linalg.norm(normal)
        mesh.GetNormalsAttr().Set(
            Vt.Vec3fArray.FromNumpy(np.tile(normal, (4, 1)).astype(np.float32))
        )
        mesh.SetNormalsInterpolation("faceVarying")
    shader = UsdShade.Shader(
        stage.GetPrimAtPath("/Appearance/Materials/SourceLeather/Surface")
    )
    color = np.array([0.59, 0.34, 0.17])
    linear = ((color + 0.055) / 1.055) ** 2.4
    shader.GetInput("diffuseColor").Set(Gf.Vec3f(*linear))
    for i in range(9):
        base = f"/Appearance/Materials/TableTop{i}"
        shader = UsdShade.Shader(stage.GetPrimAtPath(base + "/Surface"))
        texture = UsdShade.Shader(stage.GetPrimAtPath(base + "/Texture"))
        shader.GetInput("emissiveColor").DisconnectSource()
        shader.GetInput("emissiveColor").Set(Gf.Vec3f(0))
        shader.GetInput("diffuseColor").ConnectToSource(texture.ConnectableAPI(), "rgb")
        texture.GetInput("scale").Set(Gf.Vec4f(1))
    # Complete modeled seating volume: retaining old Gaussian chair fragments
    # in the same volume would create a second, noncollidable set of furniture.
    record["regions"].append(
        {
            "kind": "box",
            "id": "CompleteModeledSeating",
            "min": [-4.8, -0.28, -1.15],
            "max": [4.95, 3.55, 2.65],
        }
    )
    stage.GetRootLayer().Save()
    record.update(
        material_completion_revision=4,
        material_completions=completions,
        source_fidelity_verified=False,
    )
    write_json(directory / "appearance_reference.json", record)
    return record
