// [builtin_object_instance]
var arr2 = new Array(8);
// [bigint]
const big3 = 10n;
// [builtin_object_instance]
let f4 = WeakMap;
const o5 = new f4();
// [typed_array]
var o8 = new Float32Array(37);
__probe("step", o8, o5, f4, big3, arr2);
// [repeat_loop]
for (var i9 = 0; i9 < 23; i9++) {
    let v11 = o5.__proto__;
}
__probe("step", o8, o5, f4, big3, arr2);
// [builtin_object_instance]
const f12 = Date;
new f12();
__probe("step", f12, o8, o5, f4, big3, arr2);
// [int_array]
let arr14 = [-1, 3, 1, 7, 4, 2147483647, 3221225471, 7, 10, 8];
__probe("step", arr14, f12, o8, o5, f4, big3);
// [number_computation]
const o15 = Math;
var n17 = Array >>> Array;
var b18 = !f4;
let n19 = -n17;
!b18;
let n21 = o15.abs(n19);
let b22 = !n19;
let n23 = ~b22;
__probe("step", n23, b22, n21, n19, b18, n17);
// [array]
const arr24 = [n21, b22, Array, o15, 8];
__probe("step", arr24, n23, b22, n21, n19, b18);
// [builtin]
const f26 = escape;
__probe("step", f26, arr24, n23, b22, n21, n19);
function f27(p28, ...p29) {
    let b30 = arr2.includes();
    const f31 = Symbol;
    const s32 = " 1";
    f31(s32);
    const n34 = p29 << s32;
    const n36 = 1.5 >>> (b18 ^ 1.5);
    var o38 = {
        __proto__: arr24,
        m() {
            super[1] = f4;
        },
    };
    let s39 = "m";
    let v40 = o38[s39]();
    var arr41 = [o8, p29, p28];
    return arr14;
}
for (let i42 = 0; i42 < 100; i42++) {
    var arr44 = f27(arr14, Float32Array);
}
__probe("warm", f27, f26, arr24, n23, b22, n21);
// [java_numeric_pipeline]
var f46 = function (p47, p48) {
    let n49 = p47 + p48;
    var n50 = n49;
    const s52 = J.typeOf(n50);
    var n53 = n50 * p47;
    n50 = n53;
    const s55 = J.typeOf(n50);
    n50++;
    let s57 = J.typeOf(n50);
    var v58 = J.echo(n50);
    return [s52, s55, s57, v58, J.over(n50)];
};
var n61 = 128;
let arr65 = f46(-n61, -256);
const arr66 = arr65;
let n67 = 3;
const arr69 = f46(n67, 3);
const arr70 = arr69;
const n71 = 0.0;
let arr72 = f46(n71, arr24);
const arr73 = arr72;
__probe("step", arr73, arr72, n71, arr70, arr69, n67);
// [java_overload]
var o74 = J;
try {
    var s76 = o74.over(n67);
} catch (e77) {
    var v79 = e77.name;
}
__probe("step", o74, arr73, arr72, n71, arr70, arr69);
// [property_configuration]
o8[arr2];
Object.defineProperty(arr24, "length", { configurable: true, get: f27, set: f12 });
__probe("step", o74, arr73, arr72, n71, arr70, arr69);
// [builtin_object_instance]
new WeakMap();
__probe("step", o74, arr73, arr72, n71, arr70, arr69);
f27(arr72, f46);
