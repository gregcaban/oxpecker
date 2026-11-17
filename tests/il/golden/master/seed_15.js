// [integer]
var n0 = 4294967294;
// [symbol]
const f1 = Symbol;
// [boolean]
f1.toStringTag;
var b3 = true;
// [builtin_object_instance]
const f6 = WeakSet;
var o7 = new f6();
// [for_in_loop]
for (var i8 in o7) {
}
__probe("step", o7, f6, b3, f1, n0);
// [type_test]
let s10 = typeof 65536n;
let b12 = s10 === "function";
__probe("step", b12, s10, o7, f6, b3, f1);
// [integer]
let n13 = 0o277;
__probe("step", n13, b12, s10, o7, f6, b3);
// [binary]
var n14 = b12 / f6;
__probe("step", n14, n13, b12, s10, o7, f6);
