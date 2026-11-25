// The evBox3d + planeToCSys idiom on its own, for when a grep lands here first. COMPOSED, NOT PROBED: every line comes from source that Onshape has accepted, but this exact lambda has never been sent. Check it for one request with frc_probe_featurescript before relying on it, against a Part Studio that holds a solid body. CAUTION, measured 2026-11-25: the plane's x axis is the sketch's, so a 150x75 mm plate drawn at 30 deg on Top measures 167x140 here. For true width x height use src/skills/face_dimensions.py, which boxes along each straight edge and keeps the smallest.
// Verified against Onshape.

function(context is Context, queries)
{
    // Measure a planar face IN ITS OWN PLANE. `evBox3d` accepts a cSys, so
    // passing the face's own plane turns the result into clean u/v extents with
    // w = 0 -- which is what lets a pattern be laid out inside the face's real
    // boundary instead of inside dimensions the caller claimed.
    var faces = evaluateQuery(context, qGeometry(
        qOwnedByBody(qBodyType(qEverything(EntityType.BODY), BodyType.SOLID),
                     EntityType.FACE),
        GeometryType.PLANE));
    if (size(faces) == 0)
    {
        throw regenError("No planar face on any solid body.");
    }

    var facePlane = evPlane(context, { "face" : faces[0] });
    var faceBox = evBox3d(context, {
        "topology" : faces[0],
        "cSys" : planeToCSys(facePlane)
    });

    // `box` is a reserved word -- hence `faceBox`. See failures/.
    // Report whole millimetres: toString() on a length renders
    // "0.15000000000000002 meter", which reads as a broken tool.
    return {
        "u_mm" : round((faceBox.maxCorner[0] - faceBox.minCorner[0]) / millimeter),
        "v_mm" : round((faceBox.maxCorner[1] - faceBox.minCorner[1]) / millimeter),
        "w_mm" : round((faceBox.maxCorner[2] - faceBox.minCorner[2]) / millimeter)
    };
}
