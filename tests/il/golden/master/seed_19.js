// [boolean]
const b3 = true;
__probe("step", b3);
// [binary]
const v6 = null || null;
__probe("step", v6, b3);
// [number_computation]
var o7 = Math;
const n8 = 256;
n8 + o7;
+o7;
var n11 = o7.max(o7, n8);
__probe("step", n8, o7, v6, b3);
// [element_assignment]
n11 - b3;
o7[6] = 2;
__probe("step", n11, n8, o7, v6, b3);
// [java_numeric_pipeline]
var f15 = function (p16, p17) {
    const n18 = p16 + p17;
    let n19 = n18;
    let s21 = J.typeOf(n19);
    var n22 = ~n19;
    n19 = n22;
    let s24 = J.typeOf(n19);
    n19++;
    var s26 = J.typeOf(n19);
    var v27 = J.echo(n19);
    var s28 = J.over(n19);
    return [s21, s24, s26, v27, s28];
};
const n32 = -2147483647;
let arr34 = f15(65536, n32);
var n36 = 1;
var arr37 = f15(1, n36);
let arr38 = arr37;
var n39 = 1.5;
const arr40 = f15(n39, n36);
let arr41 = arr40;
__probe("step", arr41, arr40, n39, arr38, arr37, n36);
