<script setup lang="ts">
import { computed, nextTick, onMounted, ref } from 'vue'
import {
  AlertTriangle,
  Bot,
  CheckCircle2,
  Clock3,
  Hammer,
  MessageSquare,
  Plus,
  Send,
  Terminal,
  User,
} from '@lucide/vue'

type EventType = 'session' | 'user' | 'assistant' | 'tool_start' | 'tool_result' | 'todo' | 'compact' | 'error' | 'done'
type MessageRole = 'user' | 'assistant' | 'assistant_tool_call' | 'tool_result' | 'context_summary'

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

type ChatMessage = {
  role: MessageRole
  content: string
  tool_call_id?: string | null
  tool_name?: string | null
  is_error?: boolean
}

type SessionSummary = {
  session_id: string
  message_count: number
  todos: Array<{ content: string; status: string; activeForm?: string }>
  title: string
  updated_at: number
}

type TimelineItem = AgentEvent & { id: string; ts: number }

const API_BASE = 'http://127.0.0.1:18002'
const input = ref('')
const sessionId = ref<string | null>(null)
const sessions = ref<SessionSummary[]>([])
const timeline = ref<TimelineItem[]>([])
const todos = ref<Array<{ content: string; status: string; activeForm?: string }>>([])
const isRunning = ref(false)
const statusText = ref('Ready')
const errorText = ref('')
const scroller = ref<HTMLElement | null>(null)

const visibleMessages = computed(() => timeline.value.filter((item) => item.type !== 'session' && item.type !== 'done'))
const toolEvents = computed(() => timeline.value.filter((item) => item.type === 'tool_start' || item.type === 'tool_result'))
const completedTodos = computed(() => todos.value.filter((todo) => todo.status === 'completed').length)
const activeSession = computed(() => sessions.value.find((session) => session.session_id === sessionId.value))

function eventId() {
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`
}

function addEvent(event: AgentEvent) {
  if (event.session_id) sessionId.value = event.session_id
  if (event.type === 'todo' && Array.isArray(event.data)) {
    todos.value = event.data as Array<{ content: string; status: string; activeForm?: string }>
  }
  if (event.type === 'error') errorText.value = event.content
  if (event.type === 'done') statusText.value = 'Ready'
  timeline.value.push({ ...event, id: eventId(), ts: Date.now() })
  nextTick(() => scroller.value?.scrollTo({ top: scroller.value.scrollHeight, behavior: 'smooth' }))
}

function displayRole(type: EventType) {
  if (type === 'assistant') return 'assistant'
  if (type === 'user') return 'you'
  if (type === 'tool_start') return 'tool call'
  if (type === 'tool_result') return 'tool result'
  if (type === 'compact') return 'compact'
  if (type === 'error') return 'error'
  if (type === 'todo') return 'todo'
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

function timelineFromMessages(messages: ChatMessage[]) {
  return messages.filter((message) => !message.content.startsWith('<memory>')).map((message) => {
    const type: EventType =
      message.role === 'assistant_tool_call'
        ? 'tool_start'
        : message.role === 'tool_result'
          ? 'tool_result'
          : message.role === 'context_summary'
            ? 'compact'
            : message.role === 'assistant'
              ? 'assistant'
              : 'user'
    let input: Record<string, unknown> | undefined
    if (message.role === 'assistant_tool_call') {
      try {
        input = JSON.parse(message.content || '{}') as Record<string, unknown>
      } catch {
        input = {}
      }
    }
    return {
      id: eventId(),
      ts: Date.now(),
      type,
      content: message.content,
      tool_name: message.tool_name ?? undefined,
      tool_call_id: message.tool_call_id ?? undefined,
      input,
      is_error: message.is_error ?? false,
    } satisfies TimelineItem
  })
}

async function refreshSessions() {
  const response = await fetch(`${API_BASE}/api/sessions`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  sessions.value = await response.json() as SessionSummary[]
}

async function loadSession(id: string) {
  if (isRunning.value) return
  const response = await fetch(`${API_BASE}/api/sessions/${id}`)
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const data = await response.json() as { session_id: string; messages: ChatMessage[] }
  sessionId.value = data.session_id
  timeline.value = timelineFromMessages(data.messages)
  const session = sessions.value.find((item) => item.session_id === data.session_id)
  todos.value = session?.todos ?? []
  errorText.value = ''
  statusText.value = 'Ready'
  nextTick(() => scroller.value?.scrollTo({ top: scroller.value.scrollHeight }))
}

async function sendMessage() {
  const message = input.value.trim()
  if (!message || isRunning.value) return
  input.value = ''
  errorText.value = ''
  isRunning.value = true
  statusText.value = 'Thinking'

  try {
    const response = await fetch(`${API_BASE}/api/chat/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, session_id: sessionId.value }),
    })
    if (!response.ok || !response.body) throw new Error(`HTTP ${response.status}`)
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
        if (event.type === 'assistant') statusText.value = 'Answering'
        addEvent(event)
      }
    }
    await refreshSessions()
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
  if (event.key !== 'Enter') return
  if (event.shiftKey) return
  event.preventDefault()
  sendMessage()
}

function formatTime(ts: number) {
  if (!ts) return ''
  return new Date(ts * 1000).toLocaleString()
}

onMounted(() => {
  refreshSessions().catch((error) => {
    errorText.value = error instanceof Error ? error.message : String(error)
  })
})
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark"><Terminal :size="20" /></div>
        <div>
          <h1>code-agent</h1>
          <p>agent workspace</p>
        </div>
      </div>

      <button class="new-button" type="button" @click="newSession">
        <Plus :size="16" />
        New session
      </button>

      <section class="panel sessions-panel">
        <div class="panel-title">
          <MessageSquare :size="16" />
          Sessions
        </div>
        <button
          v-for="session in sessions"
          :key="session.session_id"
          class="session-row"
          :class="{ active: session.session_id === sessionId }"
          type="button"
          @click="loadSession(session.session_id)"
        >
          <span>{{ session.title }}</span>
          <small>{{ session.message_count }} msgs 璺?{{ formatTime(session.updated_at) }}</small>
        </button>
        <div v-if="sessions.length === 0" class="empty">No saved sessions</div>
      </section>

      <section class="panel status-panel">
        <div class="panel-title">
          <Clock3 :size="16" />
          Runtime
        </div>
        <div class="metric"><span>Status</span><strong>{{ statusText }}</strong></div>
        <div class="metric"><span>Session</span><strong>{{ sessionId ? sessionId.slice(0, 8) : 'new' }}</strong></div>
        <div class="metric"><span>Events</span><strong>{{ timeline.length }}</strong></div>
      </section>
    </aside>

    <main class="main">
      <header class="topbar">
        <div>
          <h2>{{ activeSession?.title ?? 'Agent Console' }}</h2>
          <p>Enter sends. Shift+Enter inserts a newline. Sessions are saved on the backend.</p>
        </div>
        <div class="status-pill" :class="{ running: isRunning }">{{ statusText }}</div>
      </header>

      <section ref="scroller" class="conversation">
        <div v-if="visibleMessages.length === 0" class="welcome">
          <Bot :size="38" />
          <h3>Start a saved agent session.</h3>
          <p>The agent can use files, tasks, memory, skills, tools, and background commands.</p>
        </div>

        <article v-for="item in visibleMessages" :key="item.id" class="message" :class="item.type">
          <div class="avatar">
            <User v-if="item.type === 'user'" :size="17" />
            <Hammer v-else-if="item.type === 'tool_start' || item.type === 'tool_result'" :size="17" />
            <AlertTriangle v-else-if="item.type === 'error'" :size="17" />
            <Bot v-else :size="17" />
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
          placeholder="Ask code-agent to work in the workspace..."
          rows="3"
          @keydown="onKeydown"
        />
        <button type="submit" :disabled="!input.trim() || isRunning" title="Send message">
          <Send :size="18" />
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
  </div>
</template>


