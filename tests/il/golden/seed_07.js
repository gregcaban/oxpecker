// [float]
const n0 = 1000000000.0;
// [integer]
const n1 = 256;
// [array]
let arr2 = [n0];
let arr3 = [arr2, arr2];
// [builtin_function]
let f4 = Number;
__probe("step", f4, arr3, arr2, n1, n0);
// [reassignment]
let n6 = n1;
n6 = n1;
__probe("step", n6, f4, arr3, arr2, n1, n0);
// [binary]
f4.call(arr3);
var n8 = n1 >>> f4;
__probe("step", n8, n6, f4, arr3, arr2, n1);
function f9(p10, p11, p12, p13) {
    p12 = n8;
    -f4;
    return n1;
}
function f15(p16, p17, p18, p19) {
    return "è\\å\u001e򯬟";
}
for (let i21 = 0; i21 < 10; i21++) {
    f9("è\\å\u001e򯬟", f15, n0, n6);
}
for (let i24 = 0; i24 < 10; i24++) {
    let s26 = f15(n6, n0, "è\\å\u001e򯬟", "è\\å\u001e򯬟");
}
__probe("warm", f15, f9, n8, n6, f4, arr3);
const s27 = f15(n1, n1, "è\\å\u001e򯬟", "è\\å\u001e򯬟");
f9(s27, "è\\å\u001e򯬟", n8, n0);
__probe("end", s27, f15, f9, n8, n6, f4);
