import { useState, useCallback, useRef, useEffect, useMemo, } from "react";
// ---------------------------------------------------------------------------
// useInfiniteCanvas — DOM-based infinite canvas with momentum pan, cursor-
// centered pinch zoom, spring-back boundaries, fit-to-content, and
// keyboard shortcuts. Adapted from openswarm's Dashboard canvas but
// dependency-free (no MUI) and typed for Dhee's node model.
// ---------------------------------------------------------------------------
const MIN_ZOOM = 0.15;
const MAX_ZOOM = 2.8;
const ZOOM_IN_FACTOR = 1.1;
const ZOOM_OUT_FACTOR = 1 / ZOOM_IN_FACTOR;
const FIT_PADDING = 160;
const BOUNDARY_MARGIN = 800;
const FRICTION = 0.93;
const MIN_VELOCITY = 0.5;
function clamp(val, min, max) {
    return Math.min(max, Math.max(min, val));
}
// 1–100 user setting → wheel zoom multiplier. 50 → ~0.004.
function sensitivityToMultiplier(setting) {
    return 0.00008 * setting;
}
export function useInfiniteCanvas({ zoomSensitivity = 50, contentBounds, enabled = true, initial, } = {}) {
    const viewportRef = useRef(null);
    const contentRef = useRef(null);
    const [state, setState] = useState({
        panX: initial?.panX ?? 0,
        panY: initial?.panY ?? 0,
        zoom: initial?.zoom ?? 1,
    });
    const [isPanning, setIsPanning] = useState(false);
    const [spaceHeld, setSpaceHeld] = useState(false);
    const panStartRef = useRef(null);
    const stateRef = useRef(state);
    stateRef.current = state;
    const spaceRef = useRef(false);
    const sensitivityRef = useRef(zoomSensitivity);
    sensitivityRef.current = zoomSensitivity;
    const contentBoundsRef = useRef(contentBounds);
    contentBoundsRef.current = contentBounds;
    const animFrameRef = useRef(null);
    const inertiaFrameRef = useRef(null);
    const velocityHistoryRef = useRef([]);
    const animateToRef = useRef(null);
    // ---- Animation helpers ----
    const cancelAnimation = useCallback(() => {
        if (animFrameRef.current) {
            cancelAnimationFrame(animFrameRef.current);
            animFrameRef.current = null;
        }
    }, []);
    const cancelInertia = useCallback(() => {
        if (inertiaFrameRef.current) {
            cancelAnimationFrame(inertiaFrameRef.current);
            inertiaFrameRef.current = null;
        }
    }, []);
    const animateTo = useCallback((target, duration = 320) => {
        cancelAnimation();
        const start = { ...stateRef.current };
        const startTime = performance.now();
        const step = (now) => {
            const t = Math.min((now - startTime) / duration, 1);
            const ease = 1 - Math.pow(1 - t, 3);
            setState({
                panX: start.panX + (target.panX - start.panX) * ease,
                panY: start.panY + (target.panY - start.panY) * ease,
                zoom: start.zoom + (target.zoom - start.zoom) * ease,
            });
            if (t < 1)
                animFrameRef.current = requestAnimationFrame(step);
            else
                animFrameRef.current = null;
        };
        animFrameRef.current = requestAnimationFrame(step);
    }, [cancelAnimation]);
    animateToRef.current = animateTo;
    // ---- Boundary spring-back ----
    const springBackIfNeeded = useCallback(() => {
        const bounds = contentBoundsRef.current;
        const vp = viewportRef.current;
        if (!bounds || !vp)
            return;
        const cur = stateRef.current;
        const vpW = vp.clientWidth;
        const vpH = vp.clientHeight;
        const vpLeft = -cur.panX / cur.zoom;
        const vpTop = -cur.panY / cur.zoom;
        const vpRight = vpLeft + vpW / cur.zoom;
        const vpBottom = vpTop + vpH / cur.zoom;
        const bLeft = bounds.minX - BOUNDARY_MARGIN;
        const bTop = bounds.minY - BOUNDARY_MARGIN;
        const bRight = bounds.maxX + BOUNDARY_MARGIN;
        const bBottom = bounds.maxY + BOUNDARY_MARGIN;
        let newPanX = cur.panX;
        let newPanY = cur.panY;
        if (vpRight < bLeft)
            newPanX = -(bLeft - vpW / cur.zoom) * cur.zoom;
        else if (vpLeft > bRight)
            newPanX = -bRight * cur.zoom;
        if (vpBottom < bTop)
            newPanY = -(bTop - vpH / cur.zoom) * cur.zoom;
        else if (vpTop > bBottom)
            newPanY = -bBottom * cur.zoom;
        if (newPanX !== cur.panX || newPanY !== cur.panY) {
            animateToRef.current?.({ panX: newPanX, panY: newPanY, zoom: cur.zoom }, 250);
        }
    }, []);
    // ---- Momentum ----
    const startInertia = useCallback((vx, vy) => {
        cancelInertia();
        let velocityX = vx;
        let velocityY = vy;
        const step = () => {
            velocityX *= FRICTION;
            velocityY *= FRICTION;
            if (Math.abs(velocityX) < MIN_VELOCITY && Math.abs(velocityY) < MIN_VELOCITY) {
                inertiaFrameRef.current = null;
                springBackIfNeeded();
                return;
            }
            setState((prev) => ({ ...prev, panX: prev.panX + velocityX, panY: prev.panY + velocityY }));
            inertiaFrameRef.current = requestAnimationFrame(step);
        };
        inertiaFrameRef.current = requestAnimationFrame(step);
    }, [cancelInertia, springBackIfNeeded]);
    // ---- Wheel (pinch-zoom + two-finger pan) ----
    useEffect(() => {
        const el = viewportRef.current;
        if (!el || !enabled)
            return;
        const onWheel = (e) => {
            const isPinchZoom = e.ctrlKey || e.metaKey;
            const dy = e.deltaMode === 1 ? e.deltaY * 40 : e.deltaY;
            const dx = e.deltaMode === 1 ? e.deltaX * 40 : e.deltaX;
            // Let inner scrollable elements consume the event until they hit their
            // boundary — same UX as openswarm, keeps nested lists scrollable.
            let target = e.target;
            while (target && target !== el) {
                const style = getComputedStyle(target);
                const overflowY = style.overflowY;
                const overflowX = style.overflowX;
                const canScrollY = target.scrollHeight > target.clientHeight &&
                    (overflowY === "auto" || overflowY === "scroll");
                const canScrollX = target.scrollWidth > target.clientWidth &&
                    (overflowX === "auto" || overflowX === "scroll");
                if ((canScrollY || canScrollX) && !isPinchZoom) {
                    const atYBoundary = !canScrollY ||
                        (dy > 0 && target.scrollTop + target.clientHeight >= target.scrollHeight - 1) ||
                        (dy < 0 && target.scrollTop <= 1);
                    const atXBoundary = !canScrollX ||
                        (dx > 0 && target.scrollLeft + target.clientWidth >= target.scrollWidth - 1) ||
                        (dx < 0 && target.scrollLeft <= 1);
                    if (atYBoundary && atXBoundary) {
                        target = target.parentElement;
                        continue;
                    }
                    return;
                }
                target = target.parentElement;
            }
            e.preventDefault();
            cancelInertia();
            if (isPinchZoom) {
                const rect = el.getBoundingClientRect();
                const cx = e.clientX - rect.left;
                const cy = e.clientY - rect.top;
                setState((prev) => {
                    const factor = Math.pow(2, -dy * sensitivityToMultiplier(sensitivityRef.current));
                    const newZoom = clamp(prev.zoom * factor, MIN_ZOOM, MAX_ZOOM);
                    const ratio = newZoom / prev.zoom;
                    return {
                        panX: cx - (cx - prev.panX) * ratio,
                        panY: cy - (cy - prev.panY) * ratio,
                        zoom: newZoom,
                    };
                });
            }
            else {
                setState((prev) => ({ ...prev, panX: prev.panX - dx, panY: prev.panY - dy }));
            }
        };
        el.addEventListener("wheel", onWheel, { passive: false });
        return () => el.removeEventListener("wheel", onWheel);
    }, [enabled, cancelInertia]);
    // ---- Mouse pan ----
    const handleMouseDown = useCallback((e) => {
        // Only left-button; ignore clicks on interactive elements
        if (e.button !== 0)
            return;
        const t = e.target;
        const isInteractive = t.closest("button, a, input, textarea, select, [data-canvas-draggable], [data-no-pan]");
        if (isInteractive && !spaceRef.current)
            return;
        e.preventDefault();
        cancelAnimation();
        cancelInertia();
        setIsPanning(true);
        velocityHistoryRef.current = [{ x: e.clientX, y: e.clientY, t: performance.now() }];
        panStartRef.current = {
            x: e.clientX,
            y: e.clientY,
            panX: stateRef.current.panX,
            panY: stateRef.current.panY,
        };
    }, [cancelAnimation, cancelInertia]);
    const handleMouseMove = useCallback((e) => {
        const start = panStartRef.current;
        if (!start)
            return;
        const dx = e.clientX - start.x;
        const dy = e.clientY - start.y;
        const now = performance.now();
        const history = velocityHistoryRef.current;
        history.push({ x: e.clientX, y: e.clientY, t: now });
        if (history.length > 5)
            history.shift();
        setState((prev) => ({ ...prev, panX: start.panX + dx, panY: start.panY + dy }));
    }, []);
    const handleMouseUp = useCallback(() => {
        const wasPanning = !!panStartRef.current;
        let didInertia = false;
        if (wasPanning) {
            const history = velocityHistoryRef.current;
            if (history.length >= 2) {
                const oldest = history[0];
                const newest = history[history.length - 1];
                const dt = newest.t - oldest.t;
                if (dt > 0 && dt < 200) {
                    const vx = (newest.x - oldest.x) / (dt / 16.67);
                    const vy = (newest.y - oldest.y) / (dt / 16.67);
                    if (Math.abs(vx) > MIN_VELOCITY || Math.abs(vy) > MIN_VELOCITY) {
                        startInertia(vx, vy);
                        didInertia = true;
                    }
                }
            }
            velocityHistoryRef.current = [];
        }
        panStartRef.current = null;
        setIsPanning(false);
        if (wasPanning && !didInertia)
            springBackIfNeeded();
    }, [startInertia, springBackIfNeeded]);
    useEffect(() => {
        const onUp = () => {
            if (panStartRef.current) {
                panStartRef.current = null;
                setIsPanning(false);
            }
        };
        window.addEventListener("mouseup", onUp);
        return () => window.removeEventListener("mouseup", onUp);
    }, []);
    useEffect(() => {
        return () => {
            cancelAnimation();
            cancelInertia();
        };
    }, [cancelAnimation, cancelInertia]);
    // ---- Zoom actions ----
    const zoomAround = useCallback((nextZoom, duration = 180) => {
        const prev = stateRef.current;
        const el = viewportRef.current;
        const newZoom = clamp(nextZoom, MIN_ZOOM, MAX_ZOOM);
        if (!el) {
            animateTo({ ...prev, zoom: newZoom }, duration);
            return;
        }
        const rect = el.getBoundingClientRect();
        const cx = rect.width / 2;
        const cy = rect.height / 2;
        const ratio = newZoom / prev.zoom;
        animateTo({
            panX: cx - (cx - prev.panX) * ratio,
            panY: cy - (cy - prev.panY) * ratio,
            zoom: newZoom,
        }, duration);
    }, [animateTo]);
    const zoomIn = useCallback(() => zoomAround(stateRef.current.zoom * ZOOM_IN_FACTOR), [zoomAround]);
    const zoomOut = useCallback(() => zoomAround(stateRef.current.zoom * ZOOM_OUT_FACTOR), [zoomAround]);
    const resetZoom = useCallback(() => animateTo({ panX: 0, panY: 0, zoom: 1 }), [animateTo]);
    // ---- Fit to cards (explicit rects) ----
    const fitToCards = useCallback((cardRects, opts) => {
        cancelAnimation();
        const viewport = viewportRef.current;
        if (!viewport || cardRects.length === 0) {
            setState({ panX: 0, panY: 0, zoom: 1 });
            return;
        }
        const vRect = viewport.getBoundingClientRect();
        let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
        for (const card of cardRects) {
            minX = Math.min(minX, card.x);
            minY = Math.min(minY, card.y);
            maxX = Math.max(maxX, card.x + card.width);
            maxY = Math.max(maxY, card.y + card.height);
        }
        if (!isFinite(minX)) {
            setState({ panX: 0, panY: 0, zoom: 1 });
            return;
        }
        const contentWidth = maxX - minX;
        const contentHeight = maxY - minY;
        const availW = vRect.width - FIT_PADDING * 2;
        const availH = vRect.height - FIT_PADDING * 2;
        const ceiling = opts?.maxZoom ?? 1;
        const floor = opts?.minZoom ?? MIN_ZOOM;
        const targetZoom = clamp(Math.min(availW / contentWidth, availH / contentHeight), floor, ceiling);
        const targetPanX = (vRect.width - contentWidth * targetZoom) / 2 - minX * targetZoom;
        const targetPanY = (vRect.height - contentHeight * targetZoom) / 2 - minY * targetZoom;
        const target = { panX: targetPanX, panY: targetPanY, zoom: targetZoom };
        const doAnimate = opts?.animate ?? true;
        if (doAnimate) {
            const cur = stateRef.current;
            const dPan = Math.abs(cur.panX - target.panX) + Math.abs(cur.panY - target.panY);
            const dZoom = Math.abs(cur.zoom - target.zoom);
            if (dPan < 5 && dZoom < 0.01)
                return;
            animateTo(target);
        }
        else {
            setState(target);
        }
    }, [cancelAnimation, animateTo]);
    // ---- Keyboard ----
    const zoomInRef = useRef(zoomIn);
    zoomInRef.current = zoomIn;
    const zoomOutRef = useRef(zoomOut);
    zoomOutRef.current = zoomOut;
    const resetZoomRef = useRef(resetZoom);
    resetZoomRef.current = resetZoom;
    useEffect(() => {
        const onKeyDown = (e) => {
            const t = e.target;
            const inField = t instanceof HTMLInputElement ||
                t instanceof HTMLTextAreaElement ||
                t?.isContentEditable;
            if (e.code === "Space" && !e.repeat && !inField) {
                e.preventDefault();
                spaceRef.current = true;
                setSpaceHeld(true);
            }
            if (e.ctrlKey || e.metaKey) {
                if (e.key === "0") {
                    e.preventDefault();
                    resetZoomRef.current();
                }
                else if (e.key === "=" || e.key === "+") {
                    e.preventDefault();
                    zoomInRef.current();
                }
                else if (e.key === "-") {
                    e.preventDefault();
                    zoomOutRef.current();
                }
            }
        };
        const onKeyUp = (e) => {
            if (e.code === "Space") {
                spaceRef.current = false;
                setSpaceHeld(false);
            }
        };
        window.addEventListener("keydown", onKeyDown);
        window.addEventListener("keyup", onKeyUp);
        return () => {
            window.removeEventListener("keydown", onKeyDown);
            window.removeEventListener("keyup", onKeyUp);
        };
    }, []);
    const handlers = useMemo(() => ({ onMouseDown: handleMouseDown, onMouseMove: handleMouseMove, onMouseUp: handleMouseUp }), [handleMouseDown, handleMouseMove, handleMouseUp]);
    const actions = useMemo(() => ({ zoomIn, zoomOut, resetZoom, fitToCards, animateTo, cancelAnimation, setState }), [zoomIn, zoomOut, resetZoom, fitToCards, animateTo, cancelAnimation]);
    return {
        ...state,
        isPanning,
        spaceHeld,
        viewportRef,
        contentRef,
        handlers,
        actions,
    };
}
