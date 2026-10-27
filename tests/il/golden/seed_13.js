// [builtin_object_instance]
const f0 = WeakSet;
var o1 = new f0();
// [string]
let s2 = "-0";
// [update]
let v3 = o1;
v3 %= v3;
__probe("step", v3, s2, o1, f0);
// [integer]
let n4 = 0b1010000011001100;
__probe("step", n4, v3, s2, o1, f0);
// [reassignment]
v3 = n4;
__probe("step", n4, v3, s2, o1, f0);
// [unbound_function_call]
var f5 = f0.bind(o1);
__probe("step", f5, n4, v3, s2, o1, f0);
// [number_computation]
var n6 = s2 / f5;
let n9 = 255;
let n10 = 1.7976931348623157e+308;
const n11 = Math.round(o1);
var n12 = Math.abs(n9);
var n13 = Math.floor(f0);
var n14 = Math.min(n9, f0);
let n15 = Math.floor(f0);
var n16 = -n15;
var n17 = -5;
__probe("step", n15, n14, n13, n12, n11, n10);
// [reassignment]
v3 = n9;
__probe("step", n17, n16, n15, n14, n13, n12);
function f18(p19, p20, p21) {
    p20.m = n16;
    const n25 = Math.max(Math.sqrt(5), n14);
    var n26 = 5 / 5;
    const n27 = n26 && n17;
    +n14;
    Math.imul(n26, n25);
    return n13;
}
function f30(p31, p32, p33, p34) {
    v3 += n9;
    o1.done %= 5;
    const n35 = n12 + n4;
    var f36 = function (p37) {
        let n40 = p37 + 1;
        [n40, p37];
        var v46 = __pause(undefined);
        n40 = n40 * 2;
        var arr49 = [v46, n40];
        return arr49;
    };
    const arr50 = f36(5);
    var arr51 = arr50;
    var f52 = function (p53, p54) {
        let n55 = 1;
        var n56 = n55;
        let n57 = 7;
        var n58 = n57;
        n56 = p53 >>> 0;
        var n61 = 2;
        var n62 = p53 ** n61;
        n56 = n62;
        var n63 = p54 * n56;
        n56 = n63;
        let n64 = n56 % p54;
        n58 = n64;
        return n56 + n58;
    };
    var f66 = function (p67, p68) {
        let o69 = arguments;
        let n71 = o69.length;
        var n73 = 1;
        var n74 = 7;
        var n75 = n74;
        const n76 = 0;
        n73 = p67 >>> n76;
        let n78 = 2;
        n73 = p67 ** n78;
        var n80 = p68 * n73;
        n73 = n80;
        n75 = n73 % p68;
        const n82 = n73 + n75;
        return n82;
    };
    const n83 = 0;
    var n84 = f52(n83, s2);
    let n85 = f66(n83, s2);
    var arr86 = [n84, n85];
    let arr87 = arr86;
    f36 /= f36;
    return arr86;
}
for (let i88 = 0; i88 < 100; i88++) {
    f18(s2, Math, o1);
}
for (let i91 = 0; i91 < 100; i91++) {
    f30(f5, n6, n15, 5);
}
__probe("warm", f30, f18, n17, n16, n15, n14);
f30(f30, Math, n4, n13);
let n95 = f18(s2, Math, n11);
__probe("end", n95, f30, f18, n17, n16, n15);
