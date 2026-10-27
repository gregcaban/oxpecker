// [builtin_object_instance]
var o2 = new WeakMap();
// [boolean]
let b3 = true;
// [computed_super_property]
var o6 = {
    __proto__: o2,
    m() {
        super[WeakMap];
    },
};
let v8 = o6["m"]();
__probe("step", v8, o6, b3, o2);
// [binary]
var n9 = o6 | WeakMap;
__probe("step", n9, v8, o6, b3, o2);
// [super_in_object_literal]
255n / WeakMap;
var o17 = {
    a: 256,
    get g() {
        var n14 = this.a;
        let n15 = 2;
        return n14 * n15;
    },
};
var o19 = { a: 1000000.0 };
var o26 = {
    __proto__: o17,
    m(p21) {
        super[p21] = super[p21] + 1;
        return this.a;
    },
};
var o27 = o26;
var v30 = o27["m"]("a");
var v31 = v30;
const v32 = o27.a;
var n33 = o17.a;
var n34 = o19.a;
var arr35 = [v32, n33, n34];
var arr36 = arr35;
__probe("step", arr36, arr35, n34, n33, v32, v31);
// [typed_array]
const n38 = 35;
const o39 = new Uint16Array(n38);
__probe("step", o39, n38, arr36, arr35, n34, n33);
// [builtin]
var n40 = +v8;
var f41 = Array;
__probe("step", f41, n40, o39, n38, arr36, arr35);
function f42(p43, p44, ...p45) {
    let o46 = /^a/u;
    const o47 = Math;
    let n48 = 3;
    var n49 = 8;
    o46 >>> n49;
    +o46;
    for (var i52 of arr36) {
        const arr54 = [v30, v31, o26, o39, 256];
        const n55 = arr36.length;
    }
    const s56 = "true";
    let n58 = 55;
    let o59 = new BigInt64Array(n58);
    let o62 = { length: 256 };
    var f63 = function (p64) {
        var v65 = p64.length;
        var n66 = 1;
        return v65 + n66;
    };
    let arr69 = [];
    for (let i70 = 0; i70 < 3; i70++) {
        arr69.push(f63(o62));
    }
    var f74 = () => {
        var n75 = 1000000000000.0;
        return n75;
    };
    Object.defineProperty(o62, "length", { configurable: true, get: f74 });
    var v76 = f63(o62);
    let n77 = arr69.push(v76);
    return o46;
}
function f78(p79, p80, p81, p82) {
    var arr83 = [5, -6, 7, 1, 6, 4294967296, -2, 7, 0, 3221225472];
    var n86 = Math.trunc(arr83);
    n86 >>> arr83;
    const n88 = -n86;
    for (let i91 = 0, i92 = 9; (() => {
        const b93 = i91 < i92;
    return b93;
})(); i91++, i92--) {
        o39[3] = "a";
        Object.defineProperty(o26, "value", { configurable: true, set: f42 });
    }
    return o6;
}
for (let i101 = 0; i101 < 10; i101++) {
    var o103 = f42(Uint16Array, n9, n40);
}
for (let i104 = 0; i104 < 100; i104++) {
    let o106 = f78(WeakMap, 256, WeakMap, arr36);
}
__probe("warm", f78, f42, f41, n40, o39, n38);
f78(f41, n9, o2, arr35);
f42(WeakMap, "m", n34);
