// [array]
var arr0 = [];
// [symbol]
var v3 = Symbol.for("\\");
// [builtin_object_instance]
const o6 = new WeakSet();
// [number_computation]
let n9 = 255;
const n12 = Math.sqrt(o6 && n9);
var n13 = Math.sqrt(o6);
__probe("step", n13, n12, n9, o6, v3, arr0);
// [trivial_function]
var f14 = function () {
    return n12;
};
__probe("step", f14, n13, n12, n9, o6, v3);
// [number_computation]
delete arr0[0xbfffffff];
arr0 / WeakSet;
let n19 = 65535;
const n20 = 0;
let n21 = 2.0;
const n22 = -n20;
const n23 = +"\\";
const n24 = n23 & n19;
const n25 = n23 << n19;
const n26 = "\\" / n24;
Math.trunc("\\");
Math.pow(n23, parseInt);
__probe("step", n26, n25, n24, n23, n22, n21);
// [element_assignment]
Math[5] = n26;
__probe("step", n26, n25, n24, n23, n22, n21);
function f29(p30, p31, p32, p33) {
    p33 ||= Symbol;
    var b35 = false;
    var n38 = 0;
    return n19;
}
for (let i39 = 0; i39 < 100; i39++) {
    var b42 = i39 == 60;
    if (b42) {
        [60, 0xbfffffff, n22];
        var o48 = {
            __proto__: arr0,
            m() {
                var v47 = super.toString("", b42);
            },
        };
        var s49 = "m";
        o48[s49]();
    }
    f29("\\", n24, Math, f14);
}
__probe("end", f29, n26, n25, n24, n23, n22);
