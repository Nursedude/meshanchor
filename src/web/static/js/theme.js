/**
 * MeshForge Theme Manager
 *
 * Handles dark/light mode switching with localStorage persistence.
 *
 * Usage:
 *   // Initialize (call on page load)
 *   ThemeManager.init();
 *
 *   // Toggle theme
 *   ThemeManager.toggle();
 *
 *   // Set specific theme
 *   ThemeManager.setTheme('dark');
 *   ThemeManager.setTheme('light');
 *   ThemeManager.setTheme('system');
 *
 *   // Get current theme
 *   const theme = ThemeManager.getTheme();
 */

const ThemeManager = (function() {
    'use strict';

    const STORAGE_KEY = 'meshforge-theme';
    const THEMES = ['light', 'dark', 'system'];

    /**
     * Get system preference for dark mode
     */
    function getSystemPreference() {
        if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
            return 'dark';
        }
        return 'light';
    }

    /**
     * Get saved theme from localStorage
     */
    function getSavedTheme() {
        try {
            const saved = localStorage.getItem(STORAGE_KEY);
            return THEMES.includes(saved) ? saved : 'system';
        } catch (e) {
            // localStorage may not be available
            return 'system';
        }
    }

    /**
     * Save theme to localStorage
     */
    function saveTheme(theme) {
        try {
            localStorage.setItem(STORAGE_KEY, theme);
        } catch (e) {
            // Ignore storage errors
        }
    }

    /**
     * Apply theme to document
     */
    function applyTheme(theme) {
        const root = document.documentElement;

        if (theme === 'system') {
            // Remove explicit theme, let CSS handle via media query
            root.removeAttribute('data-theme');
        } else {
            root.setAttribute('data-theme', theme);
        }

        // Dispatch event for components that need to react
        document.dispatchEvent(new CustomEvent('themechange', {
            detail: { theme: theme, resolved: getResolvedTheme() }
        }));
    }

    /**
     * Get the effective theme (resolves 'system' to actual theme)
     */
    function getResolvedTheme() {
        const theme = getSavedTheme();
        if (theme === 'system') {
            return getSystemPreference();
        }
        return theme;
    }

    /**
     * Initialize theme manager
     */
    function init() {
        // Apply saved theme
        const theme = getSavedTheme();
        applyTheme(theme);

        // Listen for system preference changes
        if (window.matchMedia) {
            window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', function(e) {
                if (getSavedTheme() === 'system') {
                    // Re-dispatch event when system preference changes
                    document.dispatchEvent(new CustomEvent('themechange', {
                        detail: { theme: 'system', resolved: e.matches ? 'dark' : 'light' }
                    }));
                }
            });
        }
    }

    /**
     * Set theme
     */
    function setTheme(theme) {
        if (!THEMES.includes(theme)) {
            console.warn('Invalid theme:', theme);
            return;
        }
        saveTheme(theme);
        applyTheme(theme);
    }

    /**
     * Get current theme setting
     */
    function getTheme() {
        return getSavedTheme();
    }

    /**
     * Toggle between light and dark
     */
    function toggle() {
        const current = getResolvedTheme();
        const next = current === 'dark' ? 'light' : 'dark';
        setTheme(next);
    }

    /**
     * Cycle through: light -> dark -> system
     */
    function cycle() {
        const current = getSavedTheme();
        const index = THEMES.indexOf(current);
        const next = THEMES[(index + 1) % THEMES.length];
        setTheme(next);
    }

    // Public API
    return {
        init: init,
        setTheme: setTheme,
        getTheme: getTheme,
        getResolvedTheme: getResolvedTheme,
        toggle: toggle,
        cycle: cycle,
        THEMES: THEMES
    };
})();

// Auto-initialize when DOM is ready
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ThemeManager.init);
} else {
    ThemeManager.init();
}
