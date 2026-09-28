<template>
  <div className="console">
    <div id="terminal"></div>
  </div>
</template>

<!--<script>-->
<!--import {Terminal} from 'xterm'-->
<!--import settings from "../settings"-->

<!--export default {-->
<!--  name: "Console",-->
<!--  mounted() {-->
<!--    this.show_terminal()-->
<!--  },-->
<!--  methods: {-->
<!--    show_terminal() {-->
<!--      // 初始化terminal窗口-->
<!--      let term = new Terminal({-->
<!--        rendererType: "canvas", //渲染类型-->
<!--        // rows: 40, //行数-->
<!--        convertEol: true, // 启用时，光标将设置为下一行的开头-->
<!--        scrollback: 100,   // 终端中的回滚量-->
<!--        disableStdin: false, //是否应禁用输入。-->
<!--        cursorStyle: 'underline', //光标样式-->
<!--        cursorBlink: true, //光标闪烁-->
<!--        theme: {-->
<!--          foreground: '#ffffff', //字体-->
<!--          background: '#060101', //背景色-->
<!--          cursor: 'help',//设置光标-->
<!--        }-->
<!--      });-->

<!--      // 建立websocket-->
<!--      term.open(document.getElementById('terminal'));-->

<!--      let ws = new WebSocket(`ws://api.uric.cn:8000/ws/ssh/${this.$route.params.host_id}/`);-->
<!--      let cmd = '';  // 拼接用户输入的命令-->

<!--      // 监听接收来自服务端响应的数据-->
<!--      ws.onmessage = function (event) {-->
<!--        if (!cmd) {-->
<!--          //所要执行的操作-->
<!--          term.write(event.data);-->
<!--        } else {-->
<!--          console.log(JSON.stringify(event.data))-->
<!--          cmd = ''-->
<!--          let res = event.data.replace(event.data.split('\r\n', 1)[0] + "\r\n", '');-->
<!--          term.write('\r\n' + res)-->
<!--        }-->
<!--      }-->

<!--      term.prompt = () => {-->
<!--        term.write('\r\n');-->
<!--        // term.write('\r\n$ ')-->
<!--      }-->
<!--      // ws.onopen = function (event){-->
<!--      //   console.log('web链接成功')-->
<!--      //   ws.send(-->
<!--      //     JSON.stringify(-->
<!--      //       cmd-->
<!--      //     )-->
<!--      //   )-->
<!--      // }-->
<!--      term.onKey(e => {-->
<!--        console.log(e.key)-->
<!--        const ev = e.domEvent-->
<!--        const printable = !ev.altKey && !ev.altGraphKey && !ev.ctrlKey && !ev.metaKey-->

<!--        // term.onData(key => {  // 粘贴的情况-->
<!--        //   if(key.length > 1) term.write(key)-->
<!--        // })-->

<!--        if (ev.key === "Enter") {-->
<!--          // 按下回车键进行指令的发送-->
<!--          ws.send(cmd);-->


<!--        } else if (ev.key === 8) {-->
<!--          // Do not delete the prompt-->
<!--          if (term._core.buffer.x > 2) {-->
<!--            // term.write('\b \b')-->
<!--            term.write(' ');-->
<!--          }-->
<!--        } else if (printable) {-->
<!--          term.write(e.key);-->
<!--          cmd += e.key-->
<!--        }-->
<!--      })-->
<!--      // term.onData(function (data) {-->
<!--      //       const printable = !data.altKey && !data.altGraphKey && !data.ctrlKey && !data.metaKey-->
<!--      //       //data为每个按键输入内容，例如按A，就传递给后端：{'flag': 1, 'data': 'a', 'cols': None, 'rows': None}-->
<!--      //       let send_data = JSON.stringify(-->
<!--      //           data-->
<!--      //       );-->
<!--      //       //向WebSocket发送消息，也就是输入的每一个按键-->
<!--      //       if (data.domEvent === "Enter"){-->
<!--      //         ws.send(send_data)-->
<!--      //       } else if(printable) {-->
<!--      //         trem.write(data.key)-->
<!--      //         send_data += data.key-->
<!--      //       }-->
<!--      //   });-->


<!--    }-->
<!--  }-->
<!--}-->
<!--</script>-->

<!--<style scoped>-->

<!--</style>-->

<script>
import 'xterm/css/xterm.css'
import {Terminal} from 'xterm'
import {FitAddon} from 'xterm-addon-fit'
import {AttachAddon} from 'xterm-addon-attach'

export default {
  name: 'Terminal',
  // data() {
  //   return {
  //     containerId: '3d407466c3df'
  //   }
  // },
  mounted() {
    var term = new Terminal();
    var fitAddon = new FitAddon()
    // let ws = new WebSocket(`ws://api.uric.cn:8000/ws/ssh/${this.$route.params.host_id}/`);
    var socket = new WebSocket(`ws://api.uric.cn:8000/ws/ssh/${this.$route.params.host_id}/`)
    var attachAddon = new AttachAddon(socket)

    term.loadAddon(attachAddon)
    term.loadAddon(fitAddon)
    term.open(document.getElementById('terminal'))
    fitAddon.fit()
    term.focus()
    socket.onopen = () => {
      socket.send('\n')
    } // 当连接建立时向终端发送一个换行符，不这么做的话最初终端是没有内容的，输入换行符可让终端显示当前用户的工作路径

    window.onresize = function () { // 窗口尺寸变化时，终端尺寸自适应
      fitAddon.fit()
    }

    this.term = term
    this.socket = socket
  },
  beforeDestroy() {
    this.socket.close()
    this.term  }
}
</script>.dispose()

<style scoped>
#terminal {
  height: 100%;
}
</style>