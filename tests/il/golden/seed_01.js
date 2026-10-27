// [symbol]
const s1 = "length";
let v2 = Symbol(s1);
// [array]
var arr3 = [];
// [builtin_object_instance]
let f5 = Date;
const o6 = new f5();
// [integer]
const n7 = 934;
__probe("step", n7, o6, f5, arr3, v2, s1);
// [ternary]
var b8 = o6 == 9007199254740991;
const v9 = b8 ? o6 : 9007199254740991;
__probe("step", v9, b8, n7, o6, f5, arr3);
// [element_retrieval]
let v10 = v9[-1];
__probe("step", v10, v9, b8, n7, o6, f5);
// [pause_in_function]
var f11 = function (p12) {
    const n14 = p12 + 1;
    let n15 = n14;
    let s16 = "abc";
    var arr17 = [n15, p12];
    const f19 = __pause;
    var v21 = f19(arr17);
    var n22 = 2;
    const n23 = n15 * n22;
    n15 = n23;
    return [v21, n15];
};
var arr25 = f11(9007199254740991);
var arr26 = arr25;
__probe("step", arr26, arr25, f11, v10, v9, b8);
function f27(p28, p29, p30) {
    const o31 = Math;
    var n32 = 65536;
    const n33 = 128;
    const b34 = !f5;
    return p30;
}
for (let i35 = 0; i35 < 100; i35++) {
    f27(s1, arr25, arr3);
}
__probe("warm", f27, arr26, arr25, f11, v10, v9);
// [generator_function]
function* f38(p39, p40, p41, p42) {
    var n43 = 0;
    var n44 = n7 >>> n43;
    o6[arr3];
    yield* [n44, f5, b8, p42, p39];
    return o6;
}
const o47 = f38(v10, arr26, n7, f27);
__probe("step", o47, f38, f27, arr26, arr25, f11);
// [trivial_function]
var f48 = function () {
    return arr3;
};
__probe("step", f48, o47, f38, f27, arr26, arr25);
// [integer]
let n49 = 0o13325;
__probe("step", n49, f48, o47, f38, f27, arr26);
// [method_call]
let n50 = arr26.indexOf(arr25);
__probe("step", n50, n49, f48, o47, f38, f27);
// [repeat_loop]
for (var i51 = 0; i51 < 86; i51++) {
}
__probe("step", n50, n49, f48, o47, f38, f27);
// [integer]
var n54 = 604;
__probe("step", n54, n50, n49, f48, o47, f38);
// [builtin]
let f55 = Int8Array;
__probe("step", f55, n54, n50, n49, f48, o47);
f27(s1, arr3, arr26);
