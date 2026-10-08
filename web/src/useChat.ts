import { useState, useCallback, useRef, useEffect } from 'react';

export interface ChatMessage {
    id: string;
    role: 'user' | 'assistant';
    content: string;
    toolCalls?: ToolCall[];
    timestamp: Date;
    isStreaming?: boolean;
}

export interface ToolCall {
    name: string;
    input: Record<string, unknown>;
    result?: Record<string, unknown>;
}

interface UseChatReturn {
    messages: ChatMessage[];
    isConnected: boolean;
    isLoading: boolean;
    conversationId: string | null;
    sendMessage: (text: string) => void;
    clearConversation: () => void;
}

export interface UseChatOptions {
    /** Copilot WebSocket endpoint. Defaults to VITE_COPILOT_WS_URL, then ws://localhost:8001/api/chat/ws. */
    wsUrl?: string;
    /** Returns the Orbit JWT for the signed-in user. Without one the copilot falls back to its local single-user mode. */
    getToken?: () => string | null | undefined;
}

const ENV_WS_URL = (import.meta as { env?: Record<string, string | undefined> }).env?.VITE_COPILOT_WS_URL;
export const DEFAULT_WS_URL = ENV_WS_URL || 'ws://localhost:8001/api/chat/ws';

export function useChat({ wsUrl = DEFAULT_WS_URL, getToken }: UseChatOptions = {}): UseChatReturn {
    const [messages, setMessages] = useState<ChatMessage[]>([]);
    const [isConnected, setIsConnected] = useState(false);
    const [isLoading, setIsLoading] = useState(false);
    const [conversationId, setConversationId] = useState<string | null>(null);
    const wsRef = useRef<WebSocket | null>(null);
    const reconnectTimeoutRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    const currentAssistantIdRef = useRef<string | null>(null);
    // Held in a ref so an inline getToken prop doesn't reconnect the socket on every render.
    const getTokenRef = useRef(getToken);
    getTokenRef.current = getToken;
    // Set on unmount so the onclose handler doesn't schedule a reconnect for a dead component.
    const disposedRef = useRef(false);

    const connect = useCallback(() => {
        if (wsRef.current?.readyState === WebSocket.OPEN) return;

        const token = getTokenRef.current?.();
        const url = token ? `${wsUrl}?token=${encodeURIComponent(token)}` : wsUrl;
        const ws = new WebSocket(url);

        ws.onopen = () => {
            setIsConnected(true);
            console.log('🔌 Connected to Orbit Copilot');
        };

        ws.onmessage = (event) => {
            const data = JSON.parse(event.data);

            switch (data.type) {
                case 'text_delta': {
                    const assistantId = currentAssistantIdRef.current;
                    if (!assistantId) break;

                    setMessages(prev =>
                        prev.map(msg =>
                            msg.id === assistantId
                                ? { ...msg, content: msg.content + data.text }
                                : msg
                        )
                    );
                    break;
                }

                case 'tool_call': {
                    const assistantId = currentAssistantIdRef.current;
                    if (!assistantId) break;

                    setMessages(prev =>
                        prev.map(msg =>
                            msg.id === assistantId
                                ? {
                                    ...msg,
                                    toolCalls: [
                                        ...(msg.toolCalls || []),
                                        { name: data.name, input: data.input },
                                    ],
                                }
                                : msg
                        )
                    );
                    break;
                }

                case 'tool_result': {
                    const assistantId = currentAssistantIdRef.current;
                    if (!assistantId) break;

                    setMessages(prev =>
                        prev.map(msg => {
                            if (msg.id !== assistantId) return msg;
                            const calls = [...(msg.toolCalls || [])];
                            const lastCallOfName = [...calls].reverse().find(c => c.name === data.name);
                            if (lastCallOfName) {
                                lastCallOfName.result = data.result;
                                
                                // Dispatch refresh event if a transaction was created via AI
                                if (data.name === 'create_transaction' && data.result?.success) {
                                    window.dispatchEvent(new Event('transaction-added'));
                                }
                            }
                            return { ...msg, toolCalls: calls };
                        })
                    );
                    break;
                }

                case 'done': {
                    const assistantId = currentAssistantIdRef.current;
                    if (assistantId) {
                        setMessages(prev =>
                            prev.map(msg =>
                                msg.id === assistantId
                                    ? { ...msg, isStreaming: false }
                                    : msg
                            )
                        );
                    }
                    setConversationId(data.conversation_id);
                    setIsLoading(false);
                    currentAssistantIdRef.current = null;
                    break;
                }

                case 'error': {
                    console.error('Chat error:', data.error);
                    setIsLoading(false);
                    const assistantId = currentAssistantIdRef.current;
                    if (assistantId) {
                        setMessages(prev =>
                            prev.map(msg =>
                                msg.id === assistantId
                                    ? {
                                        ...msg,
                                        content: '⚠️ Sorry, something went wrong. Please try again.',
                                        isStreaming: false,
                                    }
                                    : msg
                            )
                        );
                    }
                    currentAssistantIdRef.current = null;
                    break;
                }
            }
        };

        ws.onclose = () => {
            setIsConnected(false);
            console.log('🔌 Disconnected from Orbit Copilot');
            // Auto-reconnect after 3 seconds, unless we closed it on purpose.
            if (!disposedRef.current) {
                reconnectTimeoutRef.current = setTimeout(connect, 3000);
            }
        };

        ws.onerror = () => {
            setIsConnected(false);
        };

        wsRef.current = ws;
    }, [wsUrl]);

    // Connect on mount
    useEffect(() => {
        disposedRef.current = false;
        connect();
        return () => {
            disposedRef.current = true;
            clearTimeout(reconnectTimeoutRef.current);
            wsRef.current?.close();
            wsRef.current = null;
        };
    }, [connect]);

    const sendMessage = useCallback((text: string) => {
        if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
            console.warn('WebSocket not connected');
            return;
        }

        // Add user message
        const userMsg: ChatMessage = {
            id: `user-${Date.now()}`,
            role: 'user',
            content: text,
            timestamp: new Date(),
        };

        // Add placeholder assistant message for streaming
        const assistantId = `assistant-${Date.now()}`;
        const assistantMsg: ChatMessage = {
            id: assistantId,
            role: 'assistant',
            content: '',
            timestamp: new Date(),
            isStreaming: true,
        };

        currentAssistantIdRef.current = assistantId;
        setMessages(prev => [...prev, userMsg, assistantMsg]);
        setIsLoading(true);

        // Send to WebSocket
        wsRef.current.send(JSON.stringify({
            message: text,
            conversation_id: conversationId,
        }));
    }, [conversationId]);

    const clearConversation = useCallback(() => {
        setMessages([]);
        setConversationId(null);
    }, []);

    return {
        messages,
        isConnected,
        isLoading,
        conversationId,
        sendMessage,
        clearConversation,
    };
}
