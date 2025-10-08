// app/static/js/service-worker.js
self.addEventListener('push', event => {
    const data = event.data.json();
    const options = {
        body: data.body,
        icon: '/static/Logo.png', // Optional: icon to display
        data: {
            url: data.url // URL to open when notification is clicked
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