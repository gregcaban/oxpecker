// [bigint]
const big0 = 17n;
// [symbol]
let f1 = Symbol;
let s2 = "'";
f1(s2);
var f4 = Symbol;
const s5 = "1";
// [null]
f4.for(s5);
Symbol.hasInstance;
var v9 = null;
__probe("step", v9, s5, f4, s2, f1, big0);
// [number_computation]
let n12 = 4;
var n13 = Math.pow(n12, n12);
var n14 = ~f1;
!n14;
var b16 = !n13;
~n12;
__probe("step", n14, n13, n12, v9, s5, f4);
// [binary]
const n18 = s2 | b16;
__probe("step", n18, b16, n14, n13, n12, v9);
// [repeat_loop]
for (var i19 = 0; i19 < 62; i19++) {
}
__probe("step", n18, b16, n14, n13, n12, v9);
// [type_test]
const s21 = typeof n13;
var b23 = s21 === "symbol";
__probe("step", b23, s21, n18, b16, n14, n13);
// [number_computation]
var n27 = ~3221225472;
var n28 = -3221225472;
var n29 = ~Math;
var n30 = Math.min(Math, n27);
+n28;
3.0 >> n29;
var n33 = Math.abs(n30);
__probe("step", n33, n30, n29, n28, n27, b23);
function f34(p35, p36) {
    let n37 = Symbol % n14;
    p36 = s2;
    p35 %= p35;
    return n12;
}
function f39(p40, p41, p42, p43) {
    p42[0] %= n18;
    return p43;
}
for (let i45 = 0; i45 < 100; i45++) {
    f34(s2, s2);
}
for (let i48 = 0; i48 < 100; i48++) {
    f39(s2, s21, Math, Math);
}
__probe("warm", f39, f34, n33, n30, n29, n28);
var o51 = f39(s21, s2, Math, Math);
let n52 = f34(s2, s2);
__probe("end", n52, o51, f39, f34, n33, n30);
