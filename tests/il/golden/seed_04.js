// [binary]
let n2 = undefined ** 0.5;
__probe("step", n2);
// [chained_reassignments]
let n3 = 0.5;
__probe("step", n3, n2);
// [binary]
var n4 = undefined | 0.5;
__probe("step", n4, n3, n2);
// [trivial_function]
n4 || undefined;
var f6 = function () {
    return n4;
};
__probe("step", f6, n4, n3, n2);
// [builtin_object_instance]
var o8 = new Date();
// [computed_method_call]
let s9 = "getFullYear";
var v10 = o8[s9]();
__probe("step", v10, s9, o8, f6, n4, n3);
// [typed_array]
const n12 = 29;
var o13 = new Uint8ClampedArray(n12);
__probe("step", o13, n12, v10, s9, o8, f6);
// [computed_super_property]
var o15 = {
    __proto__: o8,
    m() {
        super[v10] = o8;
    },
};
var v17 = o15["m"]();
__probe("step", v17, o15, o13, n12, v10, s9);
function f18(p19, p20, p21, p22) {
    const n23 = 0x1fffffffffffff;
    var o26 = {
        __proto__: p22,
        m() {
            const v25 = super.toString(Date, Uint8ClampedArray);
        },
    };
    const s27 = "m";
    var v28 = o26[s27]();
    p20 /= n12;
    const n33 = Math.pow(p21, 4);
    const n34 = Math.min(1000000000000.0, 4);
    let n35 = Math.min(p21, n34);
    var b36 = !n4;
    var n37 = b36 << b36;
    var o38 = {};
    var o39 = {};
    var o40 = {};
    var o41 = {};
    o38.a = n2;
    o39.a = o41;
    o39.d = o41;
    o40.a = n37;
    o40.d = o8;
    o40.m = p19;
    o41.a = n34;
    o41.d = Math;
    o41.y = o8;
    return v28;
}
function f43(p44, p45, p46, p47) {
    p47 = n2;
    const v48 = v10(n12);
    [4096];
    p44 = f6;
    f6 = p47;
    p47 = f6;
    p44 = n3;
    return v48;
}
for (let i50 = 0; i50 < 10; i50++) {
    let arr52 = [];
    let v53 = f18(v17, n4, n4, arr52);
}
for (let i54 = 0; i54 < 10; i54++) {
    var arr56 = [undefined];
    const v57 = f43(arr56, n4, arr56, f18);
}
__probe("warm", f43, f18, v17, o15, o13, n12);
// [array]
f43(v10, v17, o8, Uint8ClampedArray);
var arr59 = [0.5];
f18(o13, n3, 0.5, arr59);
__probe("end", arr59, f43, f18, v17, o15, o13);
