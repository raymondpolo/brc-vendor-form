self.addEventListener('push', event => {
    let notificationData = {};

    try {
        // Try to parse the incoming data as JSON
        const data = event.data.json();
        notificationData = {
            title: data.title,
            body: data.body,
            icon: '/static/Logo.png',
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
                url: '/'
            }
        };
    }

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