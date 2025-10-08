// app/static/js/service-worker.js
self.addEventListener('push', event => {
    let data;
    try {
        // Try to parse the data as JSON, which is what the server will send
        data = event.data.json();
    } catch (e) {
        // If it fails, it's likely a plain text message from dev tools
        data = {
            title: 'Test Notification',
            body: event.data.text(),
            url: '/' // Default URL for test notifications
        };
    }

    const options = {
        body: data.body,
        icon: '/static/Logo.png', // An icon for the notification
        data: {
            url: data.url // The URL to open when the notification is clicked
        }
    };

    event.waitUntil(
        self.registration.showNotification(data.title, options)
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close(); // Close the notification
    // Open the URL associated with the notification
    event.waitUntil(
        clients.openWindow(event.notification.data.url)
    );
});