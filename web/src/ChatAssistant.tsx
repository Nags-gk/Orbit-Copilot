import { useState, useRef, useEffect } from 'react';
import { MessageSquare, X, Send, Trash2, Sparkles, Loader2, ChevronRight, Wrench, Mic, MicOff, Volume2, VolumeX } from 'lucide-react';
import { useChat, type ChatMessage, type ToolCall } from '../../hooks/useChat';
import { useVoice } from '../../hooks/useVoice';
import { cn } from '../../lib/utils';

const QUICK_ACTIONS = [
    { label: '📊 Spending summary', message: 'Show me my spending summary for this month' },
    { label: '💳 Recent transactions', message: 'Show my recent transactions' },
    { label: '🔄 My subscriptions', message: 'List all my active subscriptions' },
    { label: '📈 Category breakdown', message: 'Show my spending breakdown by category' },
];

function ToolCallBadge({ tool }: { tool: ToolCall }) {
    const [expanded, setExpanded] = useState(false);
    const toolLabels: Record<string, string> = {
        get_transactions: '📋 Fetching transactions',
        create_transaction: '➕ Creating transaction',
        get_subscriptions: '🔄 Loading subscriptions',
        cancel_subscription: '❌ Cancelling subscription',
        get_spending_summary: '📊 Calculating summary',
        get_category_breakdown: '📈 Analyzing categories',
    };

    return (
        <div className="my-1.5">
            <button
                onClick={() => setExpanded(!expanded)}
                className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-[11px] font-medium bg-primary/10 text-primary/80 hover:bg-primary/20 transition-colors"
            >
                <Wrench className="w-3 h-3" />
                {toolLabels[tool.name] || tool.name}
                {tool.result && <span className="text-green-400">✓</span>}
                <ChevronRight className={cn("w-3 h-3 transition-transform", expanded && "rotate-90")} />
            </button>
            {expanded && tool.result && (
                <pre className="mt-1 p-2 rounded-lg bg-black/30 text-[10px] text-muted-foreground overflow-x-auto max-h-32 font-mono">
                    {JSON.stringify(tool.result, null, 2)}
                </pre>
            )}
        </div>
    );
}

function MessageBubble({ message }: { message: ChatMessage }) {
    const isUser = message.role === 'user';

    return (
        <div className={cn("flex gap-3 group", isUser ? "justify-end" : "justify-start")}>
            {!isUser && (
                <div className="w-7 h-7 rounded-full bg-gradient-to-br from-primary to-accent flex items-center justify-center flex-shrink-0 mt-1 shadow-lg shadow-primary/20">
                    <Sparkles className="w-3.5 h-3.5 text-white" />
                </div>
            )}
            <div className={cn(
                "max-w-[80%] rounded-2xl px-4 py-2.5 text-sm leading-relaxed",
                isUser
                    ? "bg-primary text-primary-foreground rounded-br-md"
                    : "bg-foreground/5 border border-border text-foreground rounded-bl-md"
            )}>
                {/* Tool calls */}
                {message.toolCalls && message.toolCalls.length > 0 && (
                    <div className="mb-2">
                        {message.toolCalls.map((tool, i) => (
                            <ToolCallBadge key={i} tool={tool} />
                        ))}
                    </div>
                )}

                {/* Message text */}
                {message.content ? (
                    <div className="whitespace-pre-wrap">{message.content}</div>
                ) : message.isStreaming ? (
                    <div className="flex items-center gap-1.5 py-1">
                        <div className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.3s]" />
                        <div className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce [animation-delay:-0.15s]" />
                        <div className="w-1.5 h-1.5 rounded-full bg-primary animate-bounce" />
                    </div>
                ) : null}

                {/* Streaming cursor */}
                {message.isStreaming && message.content && (
                    <span className="inline-block w-0.5 h-4 bg-primary animate-pulse ml-0.5 align-text-bottom" />
                )}
            </div>
        </div>
    );
}

export function ChatAssistant() {
    const [isOpen, setIsOpen] = useState(false);
    const [input, setInput] = useState('');
    const { messages, isConnected, isLoading, sendMessage, clearConversation } = useChat();
    const { isListening, transcript, interimTranscript, isSupported: voiceSupported, toggleListening } = useVoice(
        (finalText) => { sendMessage(finalText); }
    );
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const inputRef = useRef<HTMLInputElement>(null);
    const [isVoiceOutputEnabled, setIsVoiceOutputEnabled] = useState(false);

    // Auto-scroll to bottom on new messages
    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    // Voice output
    useEffect(() => {
        if (!isVoiceOutputEnabled || messages.length === 0) return;

        const lastMsg = messages[messages.length - 1];
        // Only speak final assistant messages
        if (lastMsg.role === 'assistant' && !lastMsg.isStreaming && lastMsg.content) {
            window.speechSynthesis.cancel();

            const utterance = new SpeechSynthesisUtterance(lastMsg.content);
            const voices = window.speechSynthesis.getVoices();
            const preferredVoice = voices.find(v => v.lang.includes('en-GB') || v.lang.includes('en-US')) || voices[0];
            if (preferredVoice) utterance.voice = preferredVoice;

            utterance.rate = 1.05;
            utterance.pitch = 1.0;

            window.speechSynthesis.speak(utterance);
        }
    }, [messages, isVoiceOutputEnabled]);

    // Focus input and audio cleanup
    useEffect(() => {
        if (isOpen) {
            setTimeout(() => inputRef.current?.focus(), 300);
        } else {
            window.speechSynthesis.cancel();
        }
    }, [isOpen]);

    const handleSend = () => {
        const text = input.trim();
        if (!text || isLoading) return;
        setInput('');
        sendMessage(text);
    };

    const handleKeyDown = (e: React.KeyboardEvent) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    return (
        <>
            {/* Floating Action Button */}
            <button
                id="chat-fab"
                onClick={() => setIsOpen(true)}
                className={cn(
                    "fixed bottom-6 right-6 z-50 w-14 h-14 rounded-full",
                    "bg-gradient-to-br from-primary via-primary to-accent",
                    "shadow-lg shadow-primary/30 hover:shadow-xl hover:shadow-primary/40",
                    "flex items-center justify-center",
                    "hover:scale-110 active:scale-95 transition-all duration-300",
                    "group",
                    isOpen && "scale-0 opacity-0 pointer-events-none"
                )}
            >
                <MessageSquare className="w-6 h-6 text-white group-hover:rotate-12 transition-transform" />
                {/* Connection indicator */}
                <span className={cn(
                    "absolute top-1 right-1 w-3 h-3 rounded-full border-2 border-background",
                    isConnected ? "bg-green-400" : "bg-yellow-400 animate-pulse"
                )} />
            </button>

            {/* Backdrop */}
            <div
                className={cn(
                    "fixed inset-0 z-50 bg-black/40 backdrop-blur-sm transition-opacity duration-300",
                    isOpen ? "opacity-100" : "opacity-0 pointer-events-none"
                )}
                onClick={() => setIsOpen(false)}
            />

            {/* Chat Panel */}
            <div
                className={cn(
                    "fixed right-0 top-0 z-50 h-full w-full sm:w-[420px]",
                    "transition-transform duration-300 ease-out",
                    isOpen ? "translate-x-0" : "translate-x-full"
                )}
            >
                <div className="h-full flex flex-col bg-background/80 backdrop-blur-2xl border-l border-border shadow-2xl">
                    {/* Header */}
                    <div className="flex items-center justify-between px-5 py-4 border-b border-border">
                        <div className="flex items-center gap-3">
                            <div className="w-9 h-9 rounded-xl bg-gradient-to-br from-primary to-accent flex items-center justify-center shadow-lg shadow-primary/20">
                                <Sparkles className="w-4.5 h-4.5 text-white" />
                            </div>
                            <div>
                                <h2 className="font-semibold text-sm text-foreground">Orbit AI</h2>
                                <p className="text-[11px] text-muted-foreground flex items-center gap-1">
                                    <span className={cn(
                                        "w-1.5 h-1.5 rounded-full inline-block",
                                        isConnected ? "bg-green-400" : "bg-yellow-400"
                                    )} />
                                    {isConnected ? 'Connected' : 'Connecting...'}
                                </p>
                            </div>
                        </div>
                        <div className="flex items-center gap-1">
                            <button
                                onClick={() => {
                                    if (isVoiceOutputEnabled) window.speechSynthesis.cancel();
                                    setIsVoiceOutputEnabled(!isVoiceOutputEnabled);
                                }}
                                className={cn(
                                    "p-2 rounded-lg hover:bg-foreground/10 transition-colors",
                                    isVoiceOutputEnabled ? "text-primary hover:bg-primary/10" : "text-muted-foreground hover:text-foreground"
                                )}
                                title={isVoiceOutputEnabled ? 'Disable voice responses' : 'Enable voice responses'}
                            >
                                {isVoiceOutputEnabled ? <Volume2 className="w-4 h-4" /> : <VolumeX className="w-4 h-4" />}
                            </button>
                            <button
                                onClick={clearConversation}
                                className="p-2 rounded-lg hover:bg-foreground/10 text-muted-foreground hover:text-foreground transition-colors"
                                title="Clear conversation"
                            >
                                <Trash2 className="w-4 h-4" />
                            </button>
                            <button
                                onClick={() => setIsOpen(false)}
                                className="p-2 rounded-lg hover:bg-foreground/10 text-muted-foreground hover:text-foreground transition-colors"
                            >
                                <X className="w-4 h-4" />
                            </button>
                        </div>
                    </div>

                    {/* Messages Area */}
                    <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 scroll-smooth">
                        {messages.length === 0 ? (
                            <div className="flex flex-col items-center justify-center h-full text-center px-6">
                                <div className="w-16 h-16 rounded-2xl bg-gradient-to-br from-primary/20 to-accent/20 flex items-center justify-center mb-4 border border-border">
                                    <Sparkles className="w-7 h-7 text-primary" />
                                </div>
                                <h3 className="font-semibold text-foreground mb-1">Hey there! 👋</h3>
                                <p className="text-sm text-muted-foreground mb-6">
                                    I'm your AI financial assistant. Ask me about your transactions, spending, or subscriptions.
                                </p>

                                {/* Quick Actions */}
                                <div className="w-full space-y-2">
                                    {QUICK_ACTIONS.map((action, i) => (
                                        <button
                                            key={i}
                                            onClick={() => sendMessage(action.message)}
                                            className="w-full text-left px-4 py-2.5 rounded-xl bg-foreground/5 border border-border text-sm text-foreground hover:bg-foreground/10 hover:border-primary/30 transition-all duration-200 flex items-center justify-between group"
                                        >
                                            <span>{action.label}</span>
                                            <ChevronRight className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors" />
                                        </button>
                                    ))}
                                </div>
                            </div>
                        ) : (
                            <>
                                {messages.map((msg) => (
                                    <MessageBubble key={msg.id} message={msg} />
                                ))}
                                <div ref={messagesEndRef} />
                            </>
                        )}
                    </div>

                    {/* Input Area */}
                    <div className="px-4 py-3 border-t border-border">
                        {/* Voice transcript preview */}
                        {isListening && (transcript || interimTranscript) && (
                            <div className="mb-2 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/30 dark:border-red-500/20 text-sm">
                                <div className="flex items-center gap-2 mb-1">
                                    <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                                    <span className="text-[11px] font-medium text-red-600 dark:text-red-400">Listening...</span>
                                </div>
                                <p className="text-foreground/90">
                                    {transcript}
                                    {interimTranscript && (
                                        <span className="text-muted-foreground/60 italic">{interimTranscript}</span>
                                    )}
                                </p>
                            </div>
                        )}
                        <div className="flex items-center gap-2 bg-foreground/5 border border-border rounded-xl px-3 py-1 focus-within:border-primary/50 transition-colors">
                            <input
                                ref={inputRef}
                                type="text"
                                value={input}
                                onChange={(e) => setInput(e.target.value)}
                                onKeyDown={handleKeyDown}
                                placeholder={isListening ? 'Listening...' : isConnected ? 'Ask Orbit AI anything...' : 'Connecting...'}
                                disabled={!isConnected || isListening}
                                className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground/60 outline-none py-2"
                            />
                            {/* Voice button */}
                            {voiceSupported && (
                                <button
                                    onClick={toggleListening}
                                    disabled={!isConnected || isLoading}
                                    className={cn(
                                        "p-2 rounded-lg transition-all relative",
                                        isListening
                                            ? "bg-red-500/20 text-red-600 dark:text-red-400 hover:bg-red-500/30"
                                            : "text-muted-foreground hover:text-foreground hover:bg-foreground/10"
                                    )}
                                    title={isListening ? 'Stop listening' : 'Start voice input'}
                                >
                                    {isListening ? (
                                        <>
                                            <MicOff className="w-4 h-4" />
                                            <span className="absolute -top-0.5 -right-0.5 w-2.5 h-2.5 rounded-full bg-red-500 animate-pulse" />
                                        </>
                                    ) : (
                                        <Mic className="w-4 h-4" />
                                    )}
                                </button>
                            )}
                            {/* Send button */}
                            <button
                                onClick={handleSend}
                                disabled={!input.trim() || isLoading || !isConnected || isListening}
                                className={cn(
                                    "p-2 rounded-lg transition-all",
                                    input.trim() && !isLoading && !isListening
                                        ? "bg-primary text-primary-foreground hover:bg-primary/90 shadow-md shadow-primary/20"
                                        : "text-muted-foreground"
                                )}
                            >
                                {isLoading ? (
                                    <Loader2 className="w-4 h-4 animate-spin" />
                                ) : (
                                    <Send className="w-4 h-4" />
                                )}
                            </button>
                        </div>
                        <p className="text-[10px] text-muted-foreground/50 text-center mt-2">
                            Powered by Claude • Sonnet 4.6{voiceSupported ? ' • Voice enabled 🎙️' : ''}
                        </p>
                    </div>
                </div>
            </div >
        </>
    );
}
