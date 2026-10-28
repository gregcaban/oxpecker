// [builtin_object_instance]
var o2 = new WeakMap();
// [boolean]
let b3 = true;
// [computed_super_property]
var o6 = {
    __proto__: o2,
    m() {
        let v5 = super[WeakMap];
    },
};
let s7 = "m";
var v8 = o6[s7]();
__probe("step", v8, s7, o6, b3, o2);
// [binary]
let n9 = o6 | WeakMap;
__probe("step", n9, v8, s7, o6, b3, o2);
// [property_removal]
var n10 = s7 / WeakMap;
const b11 = delete o6.toString;
__probe("step", b11, n10, n9, v8, s7, o6);
// [element_assignment]
o6[1] = v8;
__probe("step", b11, n10, n9, v8, s7, o6);
// [builtin_object_instance]
var b12 = 255n === b3;
var o14 = new WeakMap();
__probe("step", o14, b12, b11, n10, n9, v8);
function f15(p16, p17) {
    /^a/u;
    let o19 = Math;
    +s7;
    s7 ** 3;
    const n24 = o19.imul(1024, s7);
    o19.floor(s7);
    let b26 = o14 == WeakMap;
    var v27 = b26 ? o14 : WeakMap;
    p17 = p16;
    p16 = p17;
    p17 = p16;
    let n29 = 3;
    var o30 = new Uint32Array(n29);
    return o30;
}
function f31(p32, p33, p34) {
    delete o2[5];
    return n10;
}
for (let i36 = 0; i36 < 10; i36++) {
    const o38 = f15(b12, n9);
}
for (let i39 = 0; i39 < 100; i39++) {
    f31(i39, s7, v8);
}
__probe("warm", f31, f15, o14, b12, b11, n10);
var n42 = f31(o14, s7, v8);
f15(b11, n42);
__probe("end", n42, f31, f15, o14, b12, b11);
