import { createApp } from 'vue'
import { createRouter, createWebHistory } from 'vue-router'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import * as Icons from '@element-plus/icons-vue'
import 'element-plus/dist/index.css'
import './style.css'
import App from './App.vue'

const routes = [
  { path: '/', redirect: '/skills' },
  { path: '/skills/:name?', component: () => import('./views/SkillsView.vue'), meta: { title: 'Skill 编辑' } },
  { path: '/deploy', component: () => import('./views/DeployView.vue'), meta: { title: '发布' } },
  { path: '/chat', component: () => import('./views/ChatView.vue'), meta: { title: '问答调试' } },
  { path: '/logs', component: () => import('./views/LogsView.vue'), meta: { title: '会话日志' } },
  { path: '/analysis/:conv?', component: () => import('./views/AnalysisView.vue'), meta: { title: '执行分析' } },
  { path: '/env', component: () => import('./views/EnvView.vue'), meta: { title: '环境' } },
]

const router = createRouter({ history: createWebHistory(), routes })
router.afterEach(to => { document.title = `${to.meta.title || ''} · Skill Lab` })

const app = createApp(App)
for (const [k, c] of Object.entries(Icons)) app.component(k, c)
app.use(ElementPlus, { locale: zhCn }).use(router).mount('#app')
