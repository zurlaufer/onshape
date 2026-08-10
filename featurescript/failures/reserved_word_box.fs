// `box` is a reserved word in FeatureScript. Naming any variable that fails the whole module, and through the Feature Studio write path it fails with no reason given at all -- only the probe reports this. Use tubeBox, faceBox, plateBounds. Hit twice: once by this project, and again by a session that had read the fact in STATUS.md and not retained it.
// Onshape said: [PARSE] mismatched input 'box' expecting ID
// KEPT AS A FAILURE. Do not copy this shape.

function(context is Context, queries)
{
    var solid = qBodyType(qEverything(EntityType.BODY), BodyType.SOLID);

    // THIS LINE IS THE BUG. `box` is a reserved word in FeatureScript.
    var box = evBox3d(context, { "topology" : solid });

    return { "x_mm" : (box.maxCorner[0] - box.minCorner[0]) / millimeter };
}
