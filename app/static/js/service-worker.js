// A log to prove the new file is running
console.log("Service Worker (v6) Loaded and Parsed.");

self.addEventListener('push', event => {
    let notificationData = {};
    
    try {
        // Try to parse the incoming data as JSON
        const data = event.data.json();
        notificationData = {
            title: data.title,
            body: data.body,
            icon: '/static/Logo.png', // Make sure this icon exists at this path
            data: {
                url: data.link
            }
        };
    } catch (e) {
        // If parsing fails, treat it as plain text (for testing)
        notificationData = {
            title: 'BRC Vendor Form',
            body: event.data.text(),
            icon: '/static/Logo.png',
            data: {
                url: '/' // Default URL
            }
        };
    }

    // Must wrap showNotification in event.waitUntil
    event.waitUntil(
        self.registration.showNotification(notificationData.title, {
            body: notificationData.body,
            icon: notificationData.icon,
            data: notificationData.data
        })
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow(event.notification.data.url)
    );
});

// This event fires when the service worker is installed
self.addEventListener('install', event => {
  console.log('Service Worker (v6) installing...');
  // Force the new service worker to activate immediately
  self.skipWaiting();
});

// This event fires when the service worker becomes active
self.addEventListener('activate', event => {
  console.log('Service Worker (v6) activating...');
  // Take control of all open pages immediately
  event.waitUntil(clients.claim());
});