module.exports = {
  presets: [
    '@vue/cli-plugin-babel/preset'
  ],
  plugins: [
    // 部分漂移的新版 node_modules 依赖含 ES2020+ 语法，
    // webpack4 的 acorn 解析不了，强制 babel 降级这些语法
    '@babel/plugin-transform-numeric-separator',
    '@babel/plugin-transform-nullish-coalescing-operator',
    '@babel/plugin-transform-optional-chaining',
    '@babel/plugin-transform-class-properties',
    '@babel/plugin-transform-private-methods',
    '@babel/plugin-transform-private-property-in-object',
    '@babel/plugin-transform-logical-assignment-operators'
  ]
}
