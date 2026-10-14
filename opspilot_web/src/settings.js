export default { // 注意，对象要抛出后，其他文件中才能引入使用
    host: process.env.VUE_APP_API_BASE || 'http://127.0.0.1:8000'
}
