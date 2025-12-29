/**
 * DoQui Dashboard - Frontend Application
 * 
 * Features:
 * - WebSocket connection for real-time updates
 * - Solar system animation with 8 planets for VAD visualization
 * - Agent control (start/stop)
 * - Speaker verification display
 */

// ============ State ============
let ws = null;
let state = {
    running: false,
    speakerVerified: false,
    speakerScore: 0,
    audioLevel: -80,
    vadSpeaking: false
};

// ============ Solar System Animation ============
const canvas = document.getElementById('solar-canvas');
const ctx = canvas.getContext('2d');

// Real solar system planets with accurate colors and relative sizes
const planets = [
    { name: 'Mercury', radius: 45, speed: 0.04, size: 3, color: '#b5b5b5', angle: 0 },
    { name: 'Venus', radius: 65, speed: 0.03, size: 5, color: '#e6c87a', angle: Math.PI / 4 },
    { name: 'Earth', radius: 85, speed: 0.025, size: 5, color: '#6b93d6', angle: Math.PI / 2 },
    { name: 'Mars', radius: 105, speed: 0.02, size: 4, color: '#c1440e', angle: Math.PI * 0.75 },
    { name: 'Jupiter', radius: 130, speed: 0.012, size: 12, color: '#d8ca9d', angle: Math.PI },
    { name: 'Saturn', radius: 155, speed: 0.009, size: 10, color: '#f4d59e', hasRings: true, angle: Math.PI * 1.25 },
    { name: 'Uranus', radius: 175, speed: 0.006, size: 7, color: '#d1e7e7', angle: Math.PI * 1.5 },
    { name: 'Neptune', radius: 192, speed: 0.004, size: 6, color: '#5b5ddf', angle: Math.PI * 1.75 }
];

const centerX = canvas.width / 2;
const centerY = canvas.height / 2;
let sunRadius = 18;
let targetSunRadius = 18;
let sunPulse = 0;
let animationSpeed = 1;

function drawSolarSystem() {
    // Clear canvas with space background
    ctx.fillStyle = '#0a0a0f';
    ctx.fillRect(0, 0, canvas.width, canvas.height);

    // Add subtle stars
    if (Math.random() > 0.98) {
        ctx.fillStyle = 'rgba(255, 255, 255, 0.3)';
        ctx.beginPath();
        ctx.arc(Math.random() * canvas.width, Math.random() * canvas.height, 0.5, 0, Math.PI * 2);
        ctx.fill();
    }

    // Smooth sun radius transition
    sunRadius += (targetSunRadius - sunRadius) * 0.1;
    sunPulse += 0.05;

    // Draw orbit paths
    planets.forEach(planet => {
        ctx.strokeStyle = 'rgba(255, 255, 255, 0.05)';
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.arc(centerX, centerY, planet.radius, 0, Math.PI * 2);
        ctx.stroke();
    });

    // ========== GLOWING SUN ==========
    const pulseOffset = Math.sin(sunPulse) * 3;
    const glowRadius = sunRadius + 15 + pulseOffset;

    // Outer glow layers
    for (let i = 5; i >= 1; i--) {
        const gradient = ctx.createRadialGradient(
            centerX, centerY, sunRadius,
            centerX, centerY, glowRadius * i * 0.6
        );

        if (state.vadSpeaking) {
            // Intense golden glow when speaking
            gradient.addColorStop(0, `rgba(255, 215, 0, ${0.3 / i})`);
            gradient.addColorStop(0.5, `rgba(255, 165, 0, ${0.2 / i})`);
            gradient.addColorStop(1, 'transparent');
        } else if (state.speakerVerified) {
            // Green tint when verified
            gradient.addColorStop(0, `rgba(255, 230, 100, ${0.25 / i})`);
            gradient.addColorStop(0.5, `rgba(100, 255, 150, ${0.15 / i})`);
            gradient.addColorStop(1, 'transparent');
        } else if (state.running) {
            // Normal yellow glow
            gradient.addColorStop(0, `rgba(255, 200, 50, ${0.2 / i})`);
            gradient.addColorStop(0.5, `rgba(255, 150, 0, ${0.1 / i})`);
            gradient.addColorStop(1, 'transparent');
        } else {
            // Dim when offline
            gradient.addColorStop(0, `rgba(150, 120, 50, ${0.1 / i})`);
            gradient.addColorStop(1, 'transparent');
        }

        ctx.fillStyle = gradient;
        ctx.beginPath();
        ctx.arc(centerX, centerY, glowRadius * i * 0.6, 0, Math.PI * 2);
        ctx.fill();
    }

    // Sun corona effect
    const coronaGradient = ctx.createRadialGradient(
        centerX, centerY, sunRadius * 0.5,
        centerX, centerY, sunRadius * 2
    );

    if (state.vadSpeaking) {
        coronaGradient.addColorStop(0, '#fff7e0');
        coronaGradient.addColorStop(0.3, '#ffd700');
        coronaGradient.addColorStop(0.6, '#ff8c00');
        coronaGradient.addColorStop(1, 'transparent');
    } else {
        coronaGradient.addColorStop(0, '#fff5d4');
        coronaGradient.addColorStop(0.3, '#ffc107');
        coronaGradient.addColorStop(0.6, '#ff9800');
        coronaGradient.addColorStop(1, 'transparent');
    }

    ctx.fillStyle = coronaGradient;
    ctx.beginPath();
    ctx.arc(centerX, centerY, sunRadius * 2 + pulseOffset, 0, Math.PI * 2);
    ctx.fill();

    // Sun surface with gradient
    const sunGradient = ctx.createRadialGradient(
        centerX - sunRadius * 0.3, centerY - sunRadius * 0.3, 0,
        centerX, centerY, sunRadius
    );
    sunGradient.addColorStop(0, '#fff9c4');
    sunGradient.addColorStop(0.5, '#ffeb3b');
    sunGradient.addColorStop(0.8, '#ffc107');
    sunGradient.addColorStop(1, '#ff9800');

    ctx.fillStyle = sunGradient;
    ctx.beginPath();
    ctx.arc(centerX, centerY, sunRadius + pulseOffset * 0.5, 0, Math.PI * 2);
    ctx.fill();

    // Sun surface details (spots)
    ctx.fillStyle = 'rgba(255, 160, 0, 0.3)';
    ctx.beginPath();
    ctx.arc(centerX + 5, centerY - 3, 4, 0, Math.PI * 2);
    ctx.fill();
    ctx.beginPath();
    ctx.arc(centerX - 7, centerY + 5, 3, 0, Math.PI * 2);
    ctx.fill();

    // ========== PLANETS ==========
    planets.forEach((planet) => {
        // Update angle based on speed and audio level
        const speedMultiplier = state.vadSpeaking ? 3 : (state.running ? animationSpeed : 0.3);
        planet.angle += planet.speed * speedMultiplier;

        const x = centerX + Math.cos(planet.angle) * planet.radius;
        const y = centerY + Math.sin(planet.angle) * planet.radius;

        // Draw Saturn's rings
        if (planet.hasRings) {
            ctx.save();
            ctx.translate(x, y);
            ctx.rotate(0.4);
            ctx.strokeStyle = 'rgba(244, 213, 158, 0.6)';
            ctx.lineWidth = 2;
            ctx.beginPath();
            ctx.ellipse(0, 0, planet.size * 1.8, planet.size * 0.5, 0, 0, Math.PI * 2);
            ctx.stroke();
            ctx.strokeStyle = 'rgba(200, 180, 140, 0.4)';
            ctx.beginPath();
            ctx.ellipse(0, 0, planet.size * 2.2, planet.size * 0.7, 0, 0, Math.PI * 2);
            ctx.stroke();
            ctx.restore();
        }

        // Planet shadow/atmosphere glow
        const planetGlow = ctx.createRadialGradient(x, y, 0, x, y, planet.size * 1.5);
        planetGlow.addColorStop(0.6, planet.color);
        planetGlow.addColorStop(1, 'transparent');
        ctx.fillStyle = planetGlow;
        ctx.beginPath();
        ctx.arc(x, y, planet.size * 1.5, 0, Math.PI * 2);
        ctx.fill();

        // Planet body with gradient for 3D effect
        const planetBody = ctx.createRadialGradient(
            x - planet.size * 0.3, y - planet.size * 0.3, 0,
            x, y, planet.size
        );
        planetBody.addColorStop(0, lightenColor(planet.color, 40));
        planetBody.addColorStop(0.7, planet.color);
        planetBody.addColorStop(1, darkenColor(planet.color, 30));

        ctx.fillStyle = planetBody;
        ctx.beginPath();
        ctx.arc(x, y, planet.size, 0, Math.PI * 2);
        ctx.fill();

        // Highlight
        ctx.fillStyle = 'rgba(255, 255, 255, 0.2)';
        ctx.beginPath();
        ctx.arc(x - planet.size * 0.3, y - planet.size * 0.3, planet.size * 0.3, 0, Math.PI * 2);
        ctx.fill();
    });

    requestAnimationFrame(drawSolarSystem);
}

// Helper functions for color manipulation
function lightenColor(color, percent) {
    const num = parseInt(color.replace('#', ''), 16);
    const amt = Math.round(2.55 * percent);
    const R = Math.min(255, (num >> 16) + amt);
    const G = Math.min(255, ((num >> 8) & 0x00FF) + amt);
    const B = Math.min(255, (num & 0x0000FF) + amt);
    return `rgb(${R}, ${G}, ${B})`;
}

function darkenColor(color, percent) {
    const num = parseInt(color.replace('#', ''), 16);
    const amt = Math.round(2.55 * percent);
    const R = Math.max(0, (num >> 16) - amt);
    const G = Math.max(0, ((num >> 8) & 0x00FF) - amt);
    const B = Math.max(0, (num & 0x0000FF) - amt);
    return `rgb(${R}, ${G}, ${B})`;
}

// Start animation
drawSolarSystem();


// ============ WebSocket Connection ============
function connect() {
    const wsUrl = `ws://${window.location.host}/ws`;
    ws = new WebSocket(wsUrl);

    ws.onopen = () => {
        console.log('WebSocket connected');
        updateConnectionStatus(true);
    };

    ws.onclose = () => {
        console.log('WebSocket disconnected');
        updateConnectionStatus(false);
        setTimeout(connect, 2000);
    };

    ws.onerror = (error) => {
        console.error('WebSocket error:', error);
    };

    ws.onmessage = (event) => {
        const msg = JSON.parse(event.data);
        handleMessage(msg);
    };
}

function handleMessage(msg) {
    switch (msg.type) {
        case 'state':
            state.running = msg.running;
            state.speakerVerified = msg.speaker_verified;
            state.speakerScore = msg.speaker_score;
            state.vadSpeaking = msg.vad_speaking;
            updateUI();
            break;

        case 'status':
            state.running = msg.running;
            updateAgentStatus();
            break;

        case 'speaker':
            state.speakerVerified = msg.verified;
            state.speakerScore = msg.score;
            updateSpeakerStatus();
            break;

        case 'vad':
            state.vadSpeaking = msg.speaking;
            updateVADStatus();
            break;

        case 'audio':
            const level = msg.level;
            animationSpeed = Math.max(0.5, Math.min(3, (level + 80) / 20));
            targetSunRadius = 15 + Math.max(0, (level + 60) / 3);
            break;
    }
}


// ============ UI Updates ============
function updateUI() {
    updateAgentStatus();
    updateSpeakerStatus();
    updateVADStatus();
}

function updateAgentStatus() {
    const statusBadge = document.getElementById('agent-status');
    const toggleBtn = document.getElementById('toggle-btn');
    const btnIcon = toggleBtn.querySelector('.btn-icon');
    const btnText = toggleBtn.querySelector('.btn-text');

    if (state.running) {
        statusBadge.textContent = 'Online';
        statusBadge.className = 'status-badge online';
        toggleBtn.className = 'toggle-btn running';
        btnIcon.textContent = '⏹';
        btnText.textContent = 'Stop Agent';
    } else {
        statusBadge.textContent = 'Offline';
        statusBadge.className = 'status-badge offline';
        toggleBtn.className = 'toggle-btn';
        btnIcon.textContent = '▶';
        btnText.textContent = 'Start Agent';
    }
}

function updateSpeakerStatus() {
    const badge = document.getElementById('speaker-badge');
    const icon = badge.querySelector('.badge-icon');
    const text = badge.querySelector('.badge-text');
    const scoreFill = document.getElementById('score-fill');
    const scoreValue = document.getElementById('score-value');

    const score = state.speakerScore;
    const percent = Math.round(score * 100);

    scoreFill.style.width = `${percent}%`;
    scoreValue.textContent = `${percent}%`;

    if (!state.running) {
        badge.className = 'speaker-badge unknown';
        icon.textContent = '❓';
        text.textContent = 'Waiting...';
    } else if (state.speakerVerified) {
        badge.className = 'speaker-badge verified';
        icon.textContent = '✅';
        text.textContent = 'Verified';
    } else {
        badge.className = 'speaker-badge locked';
        icon.textContent = '🔒';
        text.textContent = 'Locked';
    }
}

function updateVADStatus() {
    const indicator = document.getElementById('vad-indicator');
    const text = document.getElementById('vad-text');

    if (state.vadSpeaking) {
        indicator.className = 'vad-indicator speaking';
        text.textContent = 'Speaking...';
    } else {
        indicator.className = 'vad-indicator silent';
        text.textContent = 'Silent';
    }
}

function updateConnectionStatus(connected) {
    const dot = document.getElementById('connection-status');
    const text = document.getElementById('connection-text');

    if (connected) {
        dot.className = 'connection-dot connected';
        text.textContent = 'Connected';
    } else {
        dot.className = 'connection-dot disconnected';
        text.textContent = 'Disconnected';
    }
}


// ============ Agent Control ============
function toggleAgent() {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
        console.error('WebSocket not connected');
        return;
    }

    if (state.running) {
        ws.send(JSON.stringify({ type: 'stop' }));
    } else {
        ws.send(JSON.stringify({ type: 'start' }));
    }
}


// ============ Initialize ============
document.addEventListener('DOMContentLoaded', () => {
    connect();
});
