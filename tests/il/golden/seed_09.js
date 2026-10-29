// [array]
var arr0 = [];
// [symbol]
var v3 = Symbol.for("\\");
// [builtin_object_instance]
const o6 = new WeakSet();
// [number_computation]
let n9 = 2;
var n11 = Math.sqrt("\\" && "\\");
const n12 = Math.sqrt("\\");
__probe("step", n12, n11, n9, o6, v3, arr0);
// [trivial_function]
var f13 = function () {
    return n11;
};
__probe("step", f13, n12, n11, n9, o6, v3);
// [integer]
var n14 = 0xbfffffff;
__probe("step", n14, f13, n12, n11, n9, o6);
// [number_computation]
var b15 = delete o6[n14];
arr0 / parseInt;
let n19 = 0;
const n20 = 2.0;
let n21 = -n19;
const n22 = +"\\";
const n23 = n22 & 65535;
const n24 = n22 << 65535;
const n25 = "\\" / n23;
const n26 = Math.trunc("\\");
Math.pow(n22, parseInt);
__probe("step", n26, n25, n24, n23, n22, n21);
// [element_assignment]
Math[5] = n25;
__probe("step", n26, n25, n24, n23, n22, n21);
function f28(p29, p30, p31, p32) {
    p32 ||= Symbol;
    var f35 = RangeError;
    var n37 = 0;
    return 65535;
}
for (let i38 = 0; i38 < 100; i38++) {
    const n40 = 60;
    var b41 = i38 == n40;
    if (b41) {
        [n40, b15, n21];
        var o47 = {
            __proto__: arr0,
            m() {
                super.toString("", b41);
            },
        };
        var s48 = "m";
        var v49 = o47[s48]();
    }
    f28(Symbol, n23, Math, f13);
}
__probe("end", f28, n26, n25, n24, n23, n22);
