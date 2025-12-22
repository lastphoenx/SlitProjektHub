#!/bin/bash
# Docker entrypoint script to start both backend and frontend

set -e

echo "=========================================="
echo "🚀 Starting SlitProjektHub"
echo "=========================================="
echo ""

# Backend startup
echo "📦 Starting Backend (FastAPI)..."
cd /app/backend
python main.py > /tmp/backend.log 2>&1 &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID"

# Wait for backend to be ready
echo "⏳ Waiting for Backend to initialize..."
sleep 3

# Check if backend started successfully
if ! kill -0 $BACKEND_PID 2>/dev/null; then
    echo "❌ Backend failed to start!"
    echo "Log output:"
    cat /tmp/backend.log
    exit 1
fi

echo "✅ Backend started successfully"
echo ""

# Frontend startup
echo "🎨 Starting Frontend (Streamlit)..."
cd /app
streamlit run app/streamlit_app.py \
    --server.port=8501 \
    --server.address=0.0.0.0 \
    --server.headless=true \
    --server.maxUploadSize=200 \
    --browser.gatherUsageStats=false \
    --logger.level=info > /tmp/frontend.log 2>&1 &
FRONTEND_PID=$!
echo "   Frontend PID: $FRONTEND_PID"

# Wait for frontend to be ready
sleep 5

if ! kill -0 $FRONTEND_PID 2>/dev/null; then
    echo "❌ Frontend failed to start!"
    echo "Log output:"
    cat /tmp/frontend.log
    exit 1
fi

echo "✅ Frontend started successfully"
echo ""

echo "=========================================="
echo "✅ SlitProjektHub is running"
echo "=========================================="
echo ""
echo "🌐 URLs:"
echo "   Frontend:  http://localhost:8501"
echo "   Backend:   http://localhost:8000"
echo "   API Docs:  http://localhost:8000/docs"
echo ""
echo "📝 Logs:"
echo "   Backend:   /tmp/backend.log"
echo "   Frontend:  /tmp/frontend.log"
echo ""
echo "Press Ctrl+C to stop"
echo ""

# Cleanup on exit
cleanup() {
    echo ""
    echo "⏹️  Shutting down..."
    kill $BACKEND_PID 2>/dev/null || true
    kill $FRONTEND_PID 2>/dev/null || true
    wait $BACKEND_PID 2>/dev/null || true
    wait $FRONTEND_PID 2>/dev/null || true
    echo "✅ Shutdown complete"
}

trap cleanup EXIT SIGINT SIGTERM

# Wait for processes
wait $BACKEND_PID $FRONTEND_PID
