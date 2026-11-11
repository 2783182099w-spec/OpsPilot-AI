const {defineConfig} = require('@vue/cli-service')
module.exports = defineConfig({
    transpileDependencies: true,
     devServer: {
         host: "127.0.0.1",
         port: 9000,
         client: {
             overlay: {
                 // Ant Design 响应式表格可能触发浏览器非致命的尺寸观察通知。
                 // 只排除此提示，编译错误与其他运行错误仍正常显示。
                 runtimeErrors: (error) => !/^ResizeObserver loop (completed with undelivered notifications\.?|limit exceeded)$/.test(error.message || ''),
             },
         },
     },
})
