// [integer]
var n1 = 0o5616;
// [float]
const n2 = 4.94065645841246544e-324;
// [binary]
let n3 = n2 >>> n2;
__probe("step", n3, n2, n1);
// [typed_array]
var o6 = new Int16Array(8);
__probe("step", o6, n3, n2, n1);
// [property_assignment]
o6.length = n1;
__probe("step", o6, n3, n2, n1);
// [int_array]
let arr7 = [-1, 3221225471, -3];
__probe("step", arr7, o6, n3, n2, n1);
