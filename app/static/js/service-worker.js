// app/static/js/service-worker.js
self.addEventListener('push', event => {
    let data;
    try {
        data = event.data.json();
    } catch (e) {
        data = {
            title: 'Test Notification',
            body: event.data.text(),
            url: '/'
        };
    }

    const options = {
        body: data.body,
        icon: '/static/Logo.png',
        data: {
            url: data.url
        }
    };

    event.waitUntil(
        self.registration.showNotification(data.title, options)
    );
});

self.addEventListener('notificationclick', event => {
    event.notification.close();
    event.waitUntil(
        clients.openWindow(event.notification.data.url)
    );
});