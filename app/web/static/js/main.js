const tg = window.Telegram.WebApp;
tg.expand();
tg.enableClosingConfirmation();

// Telegram BackButton для навигации
function initBackButton() {
    const path = window.location.pathname;

    // Если мы НЕ на главной странице магазина
    if (path !== '/shop' && path !== '/shop/') {
        tg.BackButton.show();
        tg.BackButton.onClick(() => {
            window.history.back();
        });
    } else {
        tg.BackButton.hide();
    }
}

// Вызываем при загрузке DOM
document.addEventListener('DOMContentLoaded', initBackButton);

// Global Spring Toast
function showToast(message, type = 'success') {
    // Remove existing
    const existing = document.getElementById('custom-toast');
    if (existing) existing.remove();

    const toast = document.createElement('div');
    toast.id = 'custom-toast';
    // Spring animation class + aesthetics
    toast.className = `fixed top-6 left-1/2 transform -translate-x-1/2 bg-white text-[#3E2310] px-5 py-3 rounded-2xl shadow-xl z-[100] flex items-center space-x-3 !max-w-[90%] border border-[#F2E8DC] transition-all duration-500 ease-[cubic-bezier(0.68,-0.55,0.27,1.55)] opacity-0 translate-y-[-20px]`;

    // Icon based on type
    let icon = '✅';
    if (type === 'error') icon = '🛑';
    if (type === 'info') icon = 'ℹ️';

    toast.innerHTML = `
        <span class="text-lg">${icon}</span>
        <span class="text-sm font-bold tracking-wide">${message}</span>
    `;

    document.body.appendChild(toast);

    // Trigger animation
    setTimeout(() => {
        toast.classList.remove('opacity-0', 'translate-y-[-20px]');
    }, 50);

    // Haptic
    if (type === 'success') tg.HapticFeedback.notificationOccurred('success');
    else if (type === 'error') tg.HapticFeedback.notificationOccurred('error');

    // Remove
    setTimeout(() => {
        toast.classList.add('opacity-0', 'translate-y-[-20px]');
        setTimeout(() => toast.remove(), 500);
    }, 2500);
}

// Global Cart Badge Update
function updateCartBadge(count) {
    const badge = document.getElementById('cart-badge');
    if (!badge) return;

    if (count > 0) {
        badge.innerText = count;
        badge.classList.remove('hidden');
        // Pop animation
        badge.classList.add('scale-125');
        setTimeout(() => badge.classList.remove('scale-125'), 200);
    } else {
        badge.classList.add('hidden');
    }
}

async function addToCart(productId) {
    tg.HapticFeedback.impactOccurred('medium');

    // Optimistic UI could be here, but for now standard fetch
    try {
        const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
        const response = await fetch(`/shop/api/cart/add/${productId}`, {
            method: 'POST',
            headers: {
                'X-CSRF-Token': csrfToken
            }
        });
        const result = await response.json();

        if (result.success) {
            showToast("Добавлено в корзину");
            updateCartBadge(result.total_count);
        } else {
            showToast(result.message || "Ошибка", 'error');
        }
    } catch (e) {
        showToast("Ошибка соединения", 'error');
    }
}

async function searchProducts(query) {
    const container = document.getElementById('products-grid');
    // Simple skeleton or opacity change
    container.classList.add('opacity-50');

    try {
        const response = await fetch(`/shop/api/search?q=${query}`);
        const html = await response.text();
        container.innerHTML = html;
    } finally {
        container.classList.remove('opacity-50');
    }
}

async function filterByCategory(catId) {
    tg.HapticFeedback.selectionChanged();

    // Update Buttons Visual
    const buttons = document.querySelectorAll('#categories button');
    buttons.forEach(btn => {
        if (btn.getAttribute('data-id') === String(catId)) {
            btn.className = "chip-button active";
        } else {
            btn.className = "chip-button bg-white/70 border border-white/70 text-[#64748b] whitespace-nowrap active:bg-[#4f46e5] active:text-white active:border-[#4f46e5] transition-colors";
        }
    });

    // Fetch
    const container = document.getElementById('products-grid');
    container.classList.add('opacity-50');

    try {
        const response = await fetch(`/shop/api/products?category_id=${catId}`);
        const html = await response.text();
        container.innerHTML = html;
    } catch (e) {
        showToast("Ошибка загрузки", 'error');
    } finally {
        container.classList.remove('opacity-50');
    }
}

async function toggleFavorite(btn, productId) {
    tg.HapticFeedback.impactOccurred('light');
    const icon = btn.querySelector('i');

    if (icon.classList.contains('far')) {
        icon.classList.replace('far', 'fas');
        icon.classList.add('text-[#A33B20]');
        showToast("В избранном", 'info');
    } else {
        icon.classList.replace('fas', 'far');
        icon.classList.remove('text-[#A33B20]');
    }

    const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
    await fetch(`/shop/api/favorite/${productId}`, {
        method: 'POST',
        headers: { 'X-CSRF-Token': csrfToken }
    });
}

// Cart Page Functions
// Debounce storage
const cartDebounceTimers = {};

async function updateCartQuantity(cartId, change) {
    tg.HapticFeedback.selectionChanged();
    const qtyElem = document.getElementById(`qty-${cartId}`);
    // Get current value from DOM
    let currentQty = parseInt(qtyElem.innerText);
    let newQty = currentQty + change;

    if (newQty < 1) return;

    // Optimistic UI Update
    qtyElem.innerText = newQty;
    calculateTotal();

    // Clear previous timer for this item
    if (cartDebounceTimers[cartId]) {
        clearTimeout(cartDebounceTimers[cartId]);
    }

    // Set new timer
    cartDebounceTimers[cartId] = setTimeout(async () => {
        activeRequests++;
        updateCheckoutButtonState();
        try {
            const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
            await fetch(`/shop/api/cart/update/${cartId}?qty=${newQty}`, {
                method: 'POST',
                headers: { 'X-CSRF-Token': csrfToken }
            });
            delete cartDebounceTimers[cartId];
        } catch (e) {
            // Revert on error (not perfect but safe)
            qtyElem.innerText = currentQty;
            calculateTotal();
            showToast("Ошибка обновления", 'error');
        } finally {
            activeRequests--;
            updateCheckoutButtonState();
        }
    }, 500);
}

async function removeCartItem(cartId) {
    tg.HapticFeedback.notificationOccurred('warning');
    if (!confirm('Удалить этот кофе? 🥺')) return;

    const row = document.getElementById(`cart-item-${cartId}`);
    row.style.transform = 'scale(0.9) translateX(-100px)';
    row.style.opacity = '0';

    setTimeout(async () => {
        row.remove();
        calculateTotal();
        const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
        await fetch(`/shop/api/cart/delete/${cartId}`, {
            method: 'POST',
            headers: { 'X-CSRF-Token': csrfToken }
        });

        const items = document.querySelectorAll('.cart-item-row');
        if (items.length === 0) location.reload();
    }, 300);
}

// Active Request Counter/Lock
let activeRequests = 0;

function updateCheckoutButtonState() {
    const btn = document.getElementById('checkout-btn');
    if (!btn) return;

    if (activeRequests > 0) {
        btn.disabled = true;
        btn.classList.add('grayscale', 'opacity-70', 'cursor-wait');
        if (!btn.dataset.originalText) btn.dataset.originalText = btn.innerHTML;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Обновление...';
    } else {
        // Re-calculate checks existing state
        calculateTotal();
        // calculateTotal handles disabled state based on count, we just need to reset text/styles if count > 0
        if (!btn.disabled) {
            btn.classList.remove('grayscale', 'opacity-70', 'cursor-wait');
            // Restore text is handled by calculateTotal mostly, but let's ensure
        }
    }
}


function calculateTotal() {
    // If locked by active requests, do not unlock yet (visual calc is fine, but button interaction blocked)
    if (activeRequests > 0) return;

    let total = 0;
    const checkboxes = document.querySelectorAll('.cart-checkbox:checked');
    let count = 0;

    checkboxes.forEach(cb => {
        const cartId = cb.value;
        const price = parseInt(cb.dataset.price);
        const qty = parseInt(document.getElementById(`qty-${cartId}`).innerText);
        total += price * qty;
        count += 1;
    });

    const totalElem = document.getElementById('total-price');
    if (totalElem) totalElem.innerText = total.toLocaleString('ru-RU');

    const btn = document.getElementById('checkout-btn');
    if (btn) {
        if (count > 0) {
            btn.disabled = false;
            btn.classList.remove('grayscale', 'opacity-50', 'cursor-wait');
            btn.innerText = `Оформить (${count})`;
        } else {
            btn.disabled = true;
            btn.classList.add('grayscale', 'opacity-50');
            btn.innerText = `Выберите товары`;
        }
    }
}

// Init
document.addEventListener('DOMContentLoaded', () => {
    // Initial Calc
    if (document.querySelector('.cart-checkbox')) calculateTotal();

    // Checkout Form Logic can stay similar but styled better in HTML
});
