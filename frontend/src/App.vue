<script setup lang="ts">
import { computed, nextTick, ref } from 'vue'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Hammer,
  Plus,
  RefreshCw,
  Send,
  Terminal,
  User,
} from '@lucide/vue'

type EventType =
  | 'session'
  | 'user'
  | 'assistant'
  | 'tool_start'
  | 'tool_result'
  | 'todo'
  | 'compact'
  | 'error'
  | 'done'

type AgentEvent = {
  type: EventType
  content: string
  session_id?: string
  tool_name?: string
  tool_call_id?: string
  input?: Record<string, unknown>
  is_error?: boolean
  data?: unknown
}

type TimelineItem = AgentEvent & {
  id: string
  ts: number
}

const API_BASE = 'http://127.0.0.1:18002'
const input = ref('')
const sessionId = ref<string | null>(null)
const timeline = ref<TimelineItem[]>([])
const todos = ref<Array<{ content: string; status: string; activeForm?: string }>>([])
const isRunning = ref(false)
const statusText = ref('Ready')
const errorText = ref('')
const scroller = ref<HTMLElement | null>(null)

const visibleMessages = computed(() => timeline.value.filter((item) => item.type !== 'session' && item.type !== 'done'))
const toolEvents = computed(() => timeline.value.filter((item) => item.type === 'tool_start' || item.type === 'tool_result'))
const completedTodos = computed(() => todos.value.filter((todo) => todo.status === 'completed').length)

function addEvent(event: AgentEvent) {
  if (event.session_id) sessionId.value = event.session_id
  if (event.type === 'todo' && Array.isArray(event.data)) {
    todos.value = event.data as Array<{ content: string; status: string; activeForm?: string }>
  }
  if (event.type === 'error') errorText.value = event.content
  if (event.type === 'done') statusText.value = 'Ready'
  timeline.value.push({ ...event, id: crypto.randomUUID(), ts: Date.now() })
  nextTick(() => {
    scroller.value?.scrollTo({ top: scroller.value.scrollHeight, behavior: 'smooth' })
  })
}

function displayRole(type: EventType) {
  if (type === 'assistant') return 'Assistant'
  if (type === 'user') return 'You'
  if (type === 'tool_start') return 'Tool call'
  if (type === 'tool_result') return 'Tool result'
  if (type === 'compact') return 'Compact'
  if (type === 'error') return 'Error'
  if (type === 'todo') return 'Todo'
  return type
}

function parseSseChunk(buffer: string) {
  const events: AgentEvent[] = []
  const frames = buffer.split('\n\n')
  const remainder = frames.pop() ?? ''
  for (const frame of frames) {
    const dataLines = frame
      .split('\n')
      .filter((line) => line.startsWith('data:'))
      .map((line) => line.slice(5).trim())
    if (!dataLines.length) continue
    const payload = dataLines.join('\n')
    if (!payload || payload === '{}') continue
    events.push(JSON.parse(payload) as AgentEvent)
  }
  return { events, remainder }
}

async function sendMessage() {
  const message = input.value.trim()
  if (!message || isRunning.value) return
  input.value = ''
  errorText.value = ''
  isRunning.value = true
  statusText.value = 'Agent is thinking'

  try {
    const response = await fetch(`${API_BASE}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId.value }),
    })
    if (!response.ok || !response.body) {
      throw new Error(`HTTP ${response.status}`)
    }
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parsed = parseSseChunk(buffer)
      buffer = parsed.remainder
      for (const event of parsed.events) {
        if (event.type === 'tool_start') statusText.value = `Running ${event.tool_name}`
        if (event.type === 'assistant') statusText.value = 'Receiving answer'
        addEvent(event)
      }
    }
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error)
    addEvent({ type: 'error', content: message, is_error: true })
  } finally {
    isRunning.value = false
    statusText.value = 'Ready'
  }
}

function newSession() {
  sessionId.value = null
  timeline.value = []
  todos.value = []
  errorText.value = ''
  statusText.value = 'Ready'
}

function onKeydown(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && event.key === 'Enter') {
    sendMessage()
  }
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark"><Bot :size="22" /></div>
        <div>
          <h1>code-agent</h1>
          <p>FastAPI agent kernel</p>
        </div>
      </div>

      <button class="new-button" type="button" @click="newSession">
        <Plus :size="16" />
        New session
      </button>

      <section class="panel status-panel">
        <div class="panel-title">
          <Terminal :size="16" />
          Runtime
        </div>
        <div class="metric">
          <span>Status</span>
          <strong>{{ statusText }}</strong>
        </div>
        <div class="metric">
          <span>Session</span>
          <strong>{{ sessionId ? sessionId.slice(0, 8) : 'new' }}</strong>
        </div>
        <div class="metric">
          <span>Events</span>
          <strong>{{ timeline.length }}</strong>
        </div>
      </section>

      <section class="panel">
        <div class="panel-title">
          <CheckCircle2 :size="16" />
          Todos
        </div>
        <div v-if="todos.length === 0" class="empty">No active todos</div>
        <div v-else class="todo-summary">{{ completedTodos }} / {{ todos.length }} complete</div>
        <ul class="todo-list">
          <li v-for="todo in todos" :key="todo.content" :class="todo.status">
            <span class="todo-dot"></span>
            <span>{{ todo.content }}</span>
          </li>
        </ul>
      </section>
    </aside>

    <main class="main">
      <header class="topbar">
        <div>
          <h2>Agent Console</h2>
          <p>Chat with the coding agent and watch tool use as it happens.</p>
        </div>
        <button class="icon-button" type="button" title="Reset session" @click="newSession">
          <RefreshCw :size="18" />
        </button>
      </header>

      <section ref="scroller" class="conversation">
        <div v-if="visibleMessages.length === 0" class="welcome">
          <Bot :size="38" />
          <h3>Ask code-agent to inspect, edit, or explain workspace files.</h3>
          <p>Set backend/.env when you are ready to use a real Anthropic-compatible model.</p>
        </div>

        <article v-for="item in visibleMessages" :key="item.id" class="message" :class="item.type">
          <div class="avatar">
            <User v-if="item.type === 'user'" :size="18" />
            <Hammer v-else-if="item.type === 'tool_start' || item.type === 'tool_result'" :size="18" />
            <AlertTriangle v-else-if="item.type === 'error'" :size="18" />
            <Bot v-else :size="18" />
          </div>
          <div class="bubble">
            <div class="message-head">
              <span>{{ displayRole(item.type) }}</span>
              <code v-if="item.tool_name">{{ item.tool_name }}</code>
            </div>
            <pre v-if="item.type === 'tool_start'">{{ JSON.stringify(item.input ?? {}, null, 2) }}</pre>
            <p v-else>{{ item.content }}</p>
          </div>
        </article>
      </section>

      <form class="composer" @submit.prevent="sendMessage">
        <textarea
          v-model="input"
          :disabled="isRunning"
          placeholder="Ask the agent to work in the workspace..."
          rows="3"
          @keydown="onKeydown"
        />
        <button type="submit" :disabled="!input.trim() || isRunning" title="Send message">
          <Send :size="18" />
          Send
        </button>
      </form>
    </main>

    <aside class="rightbar">
      <section class="panel tools-panel">
        <div class="panel-title">
          <Hammer :size="16" />
          Tool Trace
        </div>
        <div v-if="toolEvents.length === 0" class="empty">No tool calls yet</div>
        <div v-for="event in toolEvents" :key="event.id" class="tool-row" :class="{ failed: event.is_error }">
          <div>
            <strong>{{ event.tool_name }}</strong>
            <span>{{ event.type === 'tool_start' ? 'started' : event.is_error ? 'failed' : 'finished' }}</span>
          </div>
          <pre>{{ event.type === 'tool_start' ? JSON.stringify(event.input ?? {}, null, 2) : event.content }}</pre>
        </div>
      </section>
    </aside>
  </div>
</template>



