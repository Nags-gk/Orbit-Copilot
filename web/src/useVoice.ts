import { useState, useCallback, useRef, useEffect } from 'react';

// ── Types ────────────────────────────────────────────────

interface SpeechRecognitionEvent {
    results: SpeechRecognitionResultList;
    resultIndex: number;
}

interface SpeechRecognitionErrorEvent {
    error: string;
    message?: string;
}

interface SpeechRecognitionInstance extends EventTarget {
    continuous: boolean;
    interimResults: boolean;
    lang: string;
    start: () => void;
    stop: () => void;
    abort: () => void;
    onresult: ((event: SpeechRecognitionEvent) => void) | null;
    onerror: ((event: SpeechRecognitionErrorEvent) => void) | null;
    onend: (() => void) | null;
    onstart: (() => void) | null;
    onspeechend: (() => void) | null;
}

declare global {
    interface Window {
        SpeechRecognition: new () => SpeechRecognitionInstance;
        webkitSpeechRecognition: new () => SpeechRecognitionInstance;
    }
}

// ── Hook ─────────────────────────────────────────────────

export interface UseVoiceReturn {
    isListening: boolean;
    transcript: string;
    interimTranscript: string;
    isSupported: boolean;
    error: string | null;
    startListening: () => void;
    stopListening: () => void;
    toggleListening: () => void;
}

/**
 * Voice input hook using Web Speech API.
 *
 * - NOT continuous: recognition runs in single-utterance mode so it
 *   auto-stops after the user pauses speaking.
 * - The final transcript is delivered via the `onFinalTranscript` callback
 *   exactly once, when the recognition session ends.
 * - A 2-second silence timer acts as a safety net to stop recognition
 *   if the browser's built-in end-of-speech detection is slow.
 */
export function useVoice(onFinalTranscript?: (text: string) => void): UseVoiceReturn {
    const [isListening, setIsListening] = useState(false);
    const [transcript, setTranscript] = useState('');
    const [interimTranscript, setInterimTranscript] = useState('');
    const [error, setError] = useState<string | null>(null);

    const recognitionRef = useRef<SpeechRecognitionInstance | null>(null);
    const onFinalRef = useRef(onFinalTranscript);
    const silenceTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
    const finalTextRef = useRef('');
    const hasSentRef = useRef(false);

    useEffect(() => {
        onFinalRef.current = onFinalTranscript;
    }, [onFinalTranscript]);

    const isSupported = typeof window !== 'undefined' && (
        'SpeechRecognition' in window || 'webkitSpeechRecognition' in window
    );

    /** Clear the silence safety timer */
    const clearSilenceTimer = useCallback(() => {
        if (silenceTimerRef.current) {
            clearTimeout(silenceTimerRef.current);
            silenceTimerRef.current = undefined;
        }
    }, []);

    /** Deliver the final transcript and reset */
    const deliverResult = useCallback(() => {
        if (hasSentRef.current) return;          // guard against double-send
        const text = finalTextRef.current.trim();
        if (text && onFinalRef.current) {
            hasSentRef.current = true;
            onFinalRef.current(text);
        }
        finalTextRef.current = '';
        setTranscript('');
        setInterimTranscript('');
    }, []);

    const getRecognition = useCallback(() => {
        if (recognitionRef.current) return recognitionRef.current;
        if (!isSupported) return null;

        const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
        const recognition = new SpeechRecognition();

        // Single-utterance mode — stops automatically after a pause
        recognition.continuous = false;
        recognition.interimResults = true;
        recognition.lang = 'en-US';

        recognition.onresult = (event: SpeechRecognitionEvent) => {
            clearSilenceTimer();

            let interim = '';
            let final = '';

            for (let i = 0; i < event.results.length; i++) {
                const result = event.results[i];
                if (result.isFinal) {
                    final += result[0].transcript;
                } else {
                    interim += result[0].transcript;
                }
            }

            if (final) {
                finalTextRef.current = final;
                setTranscript(final);
                setInterimTranscript('');
            } else {
                setInterimTranscript(interim);
            }

            // Reset silence timer — stop after 2s of no new results
            silenceTimerRef.current = setTimeout(() => {
                recognition.stop();
            }, 2000);
        };

        recognition.onerror = (event: SpeechRecognitionErrorEvent) => {
            if (event.error === 'no-speech' || event.error === 'aborted') {
                return;
            }
            setError(`Voice error: ${event.error}`);
            setIsListening(false);
            clearSilenceTimer();
        };

        recognition.onend = () => {
            setIsListening(false);
            clearSilenceTimer();
            deliverResult();
        };

        recognition.onstart = () => {
            setIsListening(true);
            setError(null);
        };

        recognitionRef.current = recognition;
        return recognition;
    }, [isSupported, clearSilenceTimer, deliverResult]);

    const startListening = useCallback(() => {
        const recognition = getRecognition();
        if (!recognition) {
            setError('Speech recognition not supported in this browser');
            return;
        }

        // Reset state for a fresh session
        finalTextRef.current = '';
        hasSentRef.current = false;
        setTranscript('');
        setInterimTranscript('');
        setError(null);

        try {
            recognition.start();
        } catch {
            // Already started — ignore
        }
    }, [getRecognition]);

    const stopListening = useCallback(() => {
        clearSilenceTimer();
        recognitionRef.current?.stop();       // onend will fire → deliverResult
    }, [clearSilenceTimer]);

    const toggleListening = useCallback(() => {
        if (isListening) {
            stopListening();
        } else {
            startListening();
        }
    }, [isListening, startListening, stopListening]);

    useEffect(() => {
        return () => {
            clearSilenceTimer();
            recognitionRef.current?.abort();
        };
    }, [clearSilenceTimer]);

    return {
        isListening,
        transcript,
        interimTranscript,
        isSupported,
        error,
        startListening,
        stopListening,
        toggleListening,
    };
}
