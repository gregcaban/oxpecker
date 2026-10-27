// [builtin_function]
const f0 = Boolean;
// [bigint]
let big1 = 1024n;
// [integer]
const n3 = 0b1101100011001000;
// [symbol]
const s6 = "x";
var v7 = Symbol(s6);
__probe("step", v7, s6, n3, big1, f0);
// [update]
var v8 = null;
v8 ??= null;
__probe("step", v8, v7, s6, n3, big1, f0);
function f9(p10, p11, ...p12) {
    const arr13 = [];
    var b14 = n3 == p10;
    var n16 = 0;
    return b14;
}
for (let i17 = 0; i17 < 100; i17++) {
    if (i17 == 19) {
    }
    f9(parseInt, s6, f9);
}
__probe("end", f9, v8, v7, s6, n3, big1);
