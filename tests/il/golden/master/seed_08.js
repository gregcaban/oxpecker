// [builtin_object_instance]
var n1 = 16;
const f2 = Array;
let arr3 = new f2(n1);
const o5 = new Set();
// [plain_function]
var f8 = function (p9, p10, p11) {
    [-4, 0, 127, 6, 1, -4, 10, -2, 3221225471, 4294967294];
    const s13 = arr3.toString();
    return o5;
};
var o14 = f8(n1, f2, arr3);
__probe("step", o14, f8, o5, arr3, f2, n1);
// [int_array]
var arr15 = [4, 3221225471, -8, 128, 2, 8, -7, 3, 9007199254740992, 3];
__probe("step", arr15, o14, f8, o5, arr3, f2);
// [property_update]
arr15.length += n1;
__probe("step", arr15, o14, f8, o5, arr3, f2);
// [integer]
var n16 = 0b11011110111;
__probe("step", n16, arr15, o14, f8, o5, arr3);
// [computed_property_assignment]
o14[o5] = f8;
__probe("step", n16, arr15, o14, f8, o5, arr3);
// [number_computation]
const o17 = Math;
let n18 = 64;
let n20 = 3.0;
let n21 = o17.round(16);
let n22 = n20 ** arr3;
const n23 = ~arr3;
var n24 = o17.sqrt(n23);
var b25 = !n23;
const n27 = o17.max(o17.pow(b25, 16), n23);
__probe("step", n27, b25, n24, n23, n22, n21);
// [type_test]
var s28 = typeof n22;
s28 === "symbol";
__probe("step", s28, n27, b25, n24, n23, n22);
function f32(p33, p34, p35) {
    parseFloat >> undefined;
    return p34;
}
function f37(p38, ...p39) {
    o5.add(f2);
    let n42 = 0;
    while ((() => {
        let b44 = n42 < 0;
    return b44;
})()) {
        var n49 = 9007199254740991 - -127;
        var o50 = J;
        var s52 = o50.typeOf(n49);
        var v54 = o50.echo(n49);
        const f55 = parseInt;
        n42++;
    }
    let f57 = Uint8Array;
    let n58 = 28;
    new f57(n58);
    var v60 = b25 || o14;
    f32(arr15, n21, n18);
    return n21;
}
for (let i62 = 0; i62 < 10; i62++) {
    var n64 = f32(arr15, n24, n16);
}
for (let i65 = 0; i65 < 100; i65++) {
    var n67 = f37(n23, "-0");
}
__probe("warm", f37, f32, s28, n27, b25, n24);
let n68 = f37(f37, s28);
var n69 = f32(arr15, n21, n16);
__probe("end", n69, n68, f37, f32, s28, n27);
