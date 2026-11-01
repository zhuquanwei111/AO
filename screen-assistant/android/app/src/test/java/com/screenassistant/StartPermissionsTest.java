package com.screenassistant;

import org.junit.Test;
import static org.junit.Assert.*;

public class StartPermissionsTest {
    @Test public void missingOverlayNeverRequestsProjectionOrRemoteSession() {
        StartPermissions permissions = new StartPermissions();
        assertEquals(StartPermissions.Step.OVERLAY, permissions.next(false, true, true));
        assertEquals(StartPermissions.Step.OVERLAY, permissions.next(false, true, true));
        assertEquals(StartPermissions.Step.NOTIFICATIONS, permissions.next(true, true, false));
    }
    @Test public void deniedOptionalPermissionsAreNotRequestedInAnEndlessLoop() {
        StartPermissions permissions = new StartPermissions();
        assertEquals(StartPermissions.Step.NOTIFICATIONS, permissions.next(true, true, true));
        assertEquals(StartPermissions.Step.MICROPHONE, permissions.next(true, true, true));
        assertEquals(StartPermissions.Step.PROJECTION, permissions.next(true, true, true));
        permissions.reset();
        assertEquals(StartPermissions.Step.NOTIFICATIONS, permissions.next(true, true, false));
    }
    @Test public void grantedPermissionsLeadToFreshSystemConsent() {
        assertEquals(StartPermissions.Step.PROJECTION, new StartPermissions().next(true, false, false));
    }
}
