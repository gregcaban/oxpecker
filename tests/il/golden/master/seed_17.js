// [float]
var n2 = 5e-324;
// [boolean]
const b3 = false;
// [undefined]
var v4 = undefined;
// [int_array]
let arr5 = [10, 8, 2];
__probe("step", arr5, v4, b3, n2);
// [float_array]
var arr6 = [2.2250738585072014e-308];
__probe("step", arr6, arr5, v4, b3, n2);
// [number_computation]
const n8 = 9007199254740991;
let n9 = 0.1;
var n10 = ~n8;
__probe("step", n10, n9, n8, arr6, arr5, v4);
// [computed_property_retrieval]
const n11 = arr6[b3];
__probe("step", n11, n10, n9, n8, arr6, arr5);
// [computed_property_assignment]
Math[Math] = n8;
__probe("step", n11, n10, n9, n8, arr6, arr5);
// [integer]
var n12 = 0o4311;
__probe("step", n12, n11, n10, n9, n8, arr6);
// [plain_function]
Math[n11];
var f14 = function (p15, p16, p17, p18) {
    p18 &&= arr6;
    var v19 = --p17;
    return p16;
};
const f20 = f14(f14, f14, false, arr6);
__probe("step", f20, f14, n12, n11, n10, n9);
function f21(p22, p23, p24, p25) {
    arr5[n11] = n9;
    p25[8] = n9;
    const n27 = 0;
    var n28 = n27;
    return p23;
}
for (let i29 = 0; i29 < 100; i29++) {
    const n31 = 10;
    const b34 = (i29 % n31) == 0;
    if (b34) {
        f14 ||= f14;
    }
    f21("A¦|", n12, "A¦|", arr5);
}
__probe("end", f21, f20, f14, n12, n11, n10);
