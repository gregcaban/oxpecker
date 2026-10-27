// [null]
const v0 = null;
// [reassignment]
var v2 = v0;
v2 = v0;
__probe("step", v2, v0);
// [builtin_object_instance]
var o5 = new Object();
o5.toString();
__probe("step", o5, v2, v0);
// [binary]
var n7 = 0b10111111111111111111111111111111 << o5;
__probe("step", n7, o5, v2, v0);
