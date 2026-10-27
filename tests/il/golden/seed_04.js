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
    Math.pow(p21, 1);
    const n33 = Math.min(1000000000000.0, 1);
    const n34 = Math.min(p21, n33);
    let b35 = !n4;
    var n36 = b35 << b35;
    var o37 = {};
    var o38 = {};
    var o39 = {};
    var o40 = {};
    o37.a = n2;
    o38.a = o40;
    o38.d = o40;
    o39.a = n36;
    o39.d = o8;
    o39.m = v17;
    o40.a = n33;
    o40.d = v28;
    o40.y = o8;
    return v28;
}
function f42(p43, p44, p45, p46) {
    p46 = n2;
    var v47 = v10(n12);
    const arr48 = [4096];
    p43 = f6;
    f6 = p46;
    p46 = f6;
    p43 = n3;
    return v47;
}
for (let i49 = 0; i49 < 10; i49++) {
    let v52 = f18(v17, n4, n4, []);
}
for (let i53 = 0; i53 < 10; i53++) {
    let arr55 = [undefined];
    var v56 = f42(arr55, n4, arr55, f18);
}
__probe("warm", f42, f18, v17, o15, o13, n12);
const v57 = f42(v10, v17, o8, Uint8ClampedArray);
var v59 = f18(o13, n3, 0.5, [0.5]);
__probe("end", v59, v57, f42, f18, v17, o15);
