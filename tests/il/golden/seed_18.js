// [builtin_object_instance]
var arr2 = new Array(8);
var f4 = WeakMap;
var o5 = new f4();
// [typed_array]
let n7 = 37;
const o8 = new Float32Array(n7);
__probe("step", o8, n7, o5, f4, arr2);
// [repeat_loop]
for (var i9 = 0; i9 < 23; i9++) {
    o5.__proto__;
}
__probe("step", o8, n7, o5, f4, arr2);
// [builtin_object_instance]
new Date();
__probe("step", o8, n7, o5, f4, arr2);
// [number_computation]
[-1, 3, 1, 7, 4, 2147483647, 3221225471, 7, 10, 8];
const n18 = 8 >>> 8;
let b19 = !10n;
const n20 = -n18;
!n18;
var n22 = Math.abs(n20);
var b23 = !n20;
let n24 = ~b23;
__probe("step", n24, b23, n22, n20, b19, n18);
// [array]
const arr25 = [n22, b23, Array, 0, 8];
__probe("step", arr25, n24, b23, n22, n20, b19);
// [builtin]
let f27 = escape;
__probe("step", f27, arr25, n24, b23, n22, n20);
function f28(p29, ...p30) {
    const b31 = arr2.includes();
    var s33 = " 1";
    let n35 = p30 << Symbol(s33);
    +n18;
    let s37 = "7n";
    let n38 = 0x14f9;
    let n39 = Symbol << 1.5;
    return b19;
}
for (let i40 = 0; i40 < 100; i40++) {
    const b42 = f28(arr25, f28);
}
__probe("warm", f28, f27, arr25, n24, b23, n22);
// [number_computation]
const n44 = 2147483646;
let n45 = 1.0;
const n46 = -b23;
const n47 = b23 ** b23;
__probe("step", n47, n46, n45, n44, f28, f27);
// [builtin_object_instance]
const o49 = new WeakSet();
__probe("step", o49, n47, n46, n45, n44, f28);
// [super_property_update]
var o51 = {
    __proto__: o8,
    m() {
        super.foo ||= 1.5;
    },
};
const v53 = o51["m"]();
__probe("step", v53, o51, o49, n47, n46, n45);
let b54 = f28(arr25, Array);
__probe("changed", b54, v53, o51, o49, n47, n46);
