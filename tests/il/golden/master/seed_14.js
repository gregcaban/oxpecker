// [builtin_object_instance]
var o1 = new Date();
// [string]
let s2 = "Ë·";
// [builtin_function]
var f3 = parseFloat;
// [integer]
const n5 = 0b1000110011000001;
// [element_retrieval]
var v6 = o1[2];
__probe("step", v6, n5, f3, s2, o1);
// [reassignment]
var v7 = s2;
__probe("step", v7, v6, n5, f3, s2, o1);
// [builtin_object_instance]
new Map();
__probe("step", v7, v6, n5, f3, s2, o1);
// [proxy]
var o10 = { getOwnPropertyDescriptor: Map };
let f11 = Proxy;
let o12 = new f11(o1, o10);
__probe("step", o12, f11, o10, v7, v6, n5);
// [reassignment]
v7 = s2;
__probe("step", o12, f11, o10, v7, v6, n5);
// [super_property_update]
var o14 = {
    __proto__: o10,
    m() {
        super[10] += n5;
    },
};
var v16 = o14["m"]();
__probe("step", v16, o14, o12, f11, o10, v7);
// [plain_function]
var f18 = function (p19, ...p20) {
    p20.length *= v16;
    o12.valueOf = ArrayBuffer;
    return true;
};
var b21 = f18(v6, o14);
__probe("step", f18, v16, o14, o12, f11, o10);
function f22(p23, p24, p25, p26) {
    return Date;
}
for (let i28 = 0; i28 < 100; i28++) {
    let f30 = f22(n5, f3, n5, o1);
}
__probe("warm", f22, b21, f18, v16, o14, o12);
// [complex_for_loop]
for (let [i34, i35] = (() => {
    var n32 = 0;
    const n33 = 1;
    return [n32, n33];
})(); i34 < i35; (() => {
    var n39 = i34++;
    i35--;
})()) {
    v6.then = f3;
}
__probe("step", f22, b21, f18, v16, o14, o12);
// [array]
const arr44 = [o10, n5, b21, "m", v16];
__probe("step", arr44, f22, b21, f18, v16, o14);
// [chained_reassignments]
v7 = f18;
f18 = v7;
__probe("step", arr44, f22, b21, f18, v16, o14);
// [function_call]
let s45 = Date(ArrayBuffer);
__probe("step", s45, arr44, f22, b21, f18, v16);
f22(n5, f3, v6, o10);
