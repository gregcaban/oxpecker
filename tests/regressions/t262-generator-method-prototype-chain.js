// Rhino b95f75d: a generator method defined in an object literal (`{ *method() {} }.method`)
// gets a `.prototype` property in interpreted mode (though its `[[Prototype]]` is plain
// Object.prototype rather than %GeneratorPrototype% as the spec requires), but gets *no*
// `.prototype` property at all in compiled mode:
//   interpreted: ok; `method.prototype` exists but its prototype chain is wrong
//   compiled:    TypeError: Cannot convert undefined to an object (method.prototype is undefined)
// Behavioural difference, no crash.
// Originating test: language/expressions/object/method-definition/generator-prototype-prop.js
// Found by replaying test262 (oxpecker corpus test262), 2026-10-18.
// Replay: oxpecker diff tests/regressions/t262-generator-method-prototype-chain.js --language-version 250
var GeneratorPrototype = Object.getPrototypeOf(function* () {}).prototype;
var method = { *method() {} }.method;
__probe("p", Object.getPrototypeOf(method.prototype) === GeneratorPrototype);
