// [symbol]
var f0 = Symbol;
// [array]
var v2 = f0("length");
const arr3 = [];
// [integer]
const n4 = 9007199254740991;
// [builtin_object_instance]
const o6 = new Date();
__probe("step", o6, n4, arr3, v2, f0);
// [ternary]
const b8 = o6 == n4;
let v9 = b8 ? o6 : n4;
__probe("step", v9, b8, o6, n4, arr3, v2);
// [pause_in_function]
v9[-1];
var f11 = function (p12) {
    let n15 = p12 + 1;
    let arr17 = [n15, p12];
    const v18 = undefined;
    var f19 = __pause;
    var v21 = f19(arr17);
    const n22 = 2;
    let n23 = n15 * n22;
    n15 = n23;
    var arr24 = [v21, n15];
    return arr24;
};
var arr26 = f11(n4);
__probe("step", arr26, f11, v9, b8, o6, n4);
function f27(p28, p29, p30) {
    let n32 = 128;
    Math.max(v9, n32);
    var n36 = Math.imul(9007199254740992.0, 9007199254740992.0);
    !9007199254740992.0;
    +arr3;
    v9 instanceof Date;
    const v40 = o6[n4];
    return b8;
}
for (let i41 = 0; i41 < 10; i41++) {
    const b43 = f27("length", arr26, arr3);
}
__probe("warm", f27, arr26, f11, v9, b8, o6);
// [trivial_function]
var f45 = function () {
    return v2;
};
__probe("step", f45, f27, arr26, f11, v9, b8);
[1.0, 1e-15, 1.5];
f27("length", arr26, arr26);
