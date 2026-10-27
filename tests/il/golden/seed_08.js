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
let n18 = 4294967296;
let n20 = o17.min(n18, 64);
let n21 = o17.min("򄵵ÅMþ", n20);
let n22 = 64 * "򄵵ÅMþ";
const n23 = +n22;
var n24 = o17.pow(n21, n22);
var b25 = !64;
o17.max(b25, n22);
__probe("step", b25, n24, n23, n22, n21, n20);
// [type_test]
const s27 = typeof n21;
var s28 = "symbol";
s27 === s28;
__probe("step", s28, s27, b25, n24, n23, n22);
function f31(p32, p33, p34) {
    parseFloat >> undefined;
    return p33;
}
function f36(p37, ...p38) {
    o5.add(f2);
    let n41 = 0;
    while (n41 < 0) {
        let n45 = 9007199254740991;
        var n48 = n45 - -127;
        var s50 = J.typeOf(n48);
        var s51 = s50;
        const v52 = J.echo(n48);
        var v53 = v52;
        const n55 = n41++;
    }
    let n57 = 28;
    let o58 = new Uint8Array(n57);
    n24 || o14;
    var n60 = f31(arr15, n20, n18);
    return n20;
}
for (let i61 = 0; i61 < 10; i61++) {
    f31(arr15, n23, n16);
}
for (let i64 = 0; i64 < 100; i64++) {
    var n66 = f36(n23, "-0");
}
__probe("warm", f36, f31, s28, s27, b25, n24);
var n67 = f36(f36, s27);
let n68 = f31(arr15, n20, n16);
__probe("end", n68, n67, f36, f31, s28, s27);
