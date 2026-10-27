// [builtin_object_instance]
var f0 = WeakSet;
const o1 = new f0();
// [update]
let v3 = o1;
v3 %= v3;
__probe("step", v3, o1, f0);
// [integer]
var n4 = 0b1010000011001100;
__probe("step", n4, v3, o1, f0);
// [reassignment]
v3 = n4;
__probe("step", n4, v3, o1, f0);
// [unbound_function_call]
var f5 = f0.bind(o1);
__probe("step", f5, n4, v3, o1, f0);
// [binary]
const n6 = "-0" / f5;
__probe("step", n6, f5, n4, v3, o1, f0);
// [number_computation]
const n8 = 2147483647;
const n10 = Math.imul(5, f0);
__probe("step", n10, n8, n6, f5, n4, v3);
// [object_literal]
var o15 = {
    c: v3,
    f: 5,
    d: f5,
    y(p12) {
        return 5;
    },
    set e(p14) {
        v3 = n6;
    },
};
__probe("step", o15, n10, n8, n6, f5, n4);
function f16(p17, p18, p19, p20) {
    return p20;
}
function f22(p23, p24, p25, p26) {
    var o28 = {
        __proto__: p25,
        m() {
            super.prototype ||= v3;
        },
    };
    var v30 = o28["m"]();
    const n31 = o28 ** f16;
    try {
        let big34 = J.takesBigInteger(p26);
    } catch (e35) {
        let v37 = e35.name;
    }
    return v30;
}
for (let i38 = 0; i38 < 10; i38++) {
    f16([f0, i38, f22, f0], Math, "-0", "-0");
}
for (let i42 = 0; i42 < 10; i42++) {
    let v44 = f22("-0", n10, Math, i42);
}
__probe("warm", f22, f16, o15, n10, n8, n6);
// [array]
var arr46 = [n6, Math, f22("-0", 5, o1, f0)];
f16(arr46, arr46, "-0", "-0");
__probe("end", arr46, f22, f16, o15, n10, n8);
