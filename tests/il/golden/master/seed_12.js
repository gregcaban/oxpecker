// [builtin_object_instance]
var f0 = Object;
var o1 = new f0();
// [boolean]
let b5 = false;
// [string]
let s6 = "ã\u0015\u0007";
__probe("step", s6, b5, o1, f0);
// [unary]
let n7 = 0o400000000000000000;
n7--;
__probe("step", n7, s6, b5, o1, f0);
// [symbol]
let f9 = Symbol;
var s10 = "\"";
f9(s10);
__probe("step", s10, f9, n7, s6, b5, o1);
function f12(p13, p14, p15) {
    var o17 = {
        __proto__: o1,
        m() {
            super.b >>= 2.2250738585072014e-308;
        },
    };
    o17["m"]();
    var n20 = f9 & 2.2250738585072014e-308;
    for (let i21 in o1) {
        o1["m"] -= p15;
        var f24 = function (p25, p26) {
            let n28 = p25 + p26;
            const s30 = J.typeOf(n28);
            n28 = n28 / p25;
            const s33 = J.typeOf(n28);
            n28++;
            const s35 = J.typeOf(n28);
            let v36 = J.echo(n28);
            var arr38 = [s30, s33, s35, v36, J.over(n28)];
            return arr38;
        };
        const n40 = 0.1;
        const v42 = f24(4294967295, n40);
        var n44 = 3;
        let v45 = f24(2, n44);
        const v46 = v45;
        let n47 = 1.5;
        const v49 = f24(n47, v46);
    }
    p13 = "m";
    var n51 = 0;
    return n51;
}
for (let i52 = 0; i52 < 100; i52++) {
    let n54 = 13;
    var b55 = i52 == n54;
    if (b55) {
        o1[b55] = n54;
        delete o1[0];
        [parseFloat, f12];
    }
    f12(s10, o1, 2.2250738585072014e-308);
}
__probe("end", f12, s10, f9, n7, s6, b5);
