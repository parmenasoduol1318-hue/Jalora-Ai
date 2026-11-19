function argumentText(
  argumentsValue: Record<string, unknown>,
  key: string,
): string {
  const value = argumentsValue[key];
  return typeof value === "string" ? value.trim() : "";
}

export function approvalDescription(
  toolName: string,
  argumentsValue: Record<string, unknown> | null | undefined,
): string | null {
  const args = argumentsValue ?? {};
  const action = argumentText(args, "action");
  const appName = argumentText(args, "app_name");
  const device = argumentText(args, "device_name") || "your connected phone";

  if (
    (toolName === "open_paired_android_app" || action === "android.launcher.open")
    && appName
  ) {
    const followUp = argumentText(args, "follow_up_request");
    return followUp
      ? `Jalora wants to open ${appName} on ${device}, then ${followUp}.`
      : `Jalora wants to open ${appName} on ${device}.`;
  }

  if (toolName === "request_remote_android_action") {
    const target = argumentText(args, "name") || argumentText(args, "automation_id");
    if (action === "android.screen.click" && target) {
      return `Jalora wants to select "${target}" on ${device}.`;
    }
    if (action === "android.screen.long_press" && target) {
      return `Jalora wants to long-press "${target}" on ${device}.`;
    }
    if (action === "android.screen.tap") {
      return `Jalora wants to tap the requested screen coordinates on ${device}.`;
    }
    if (action === "android.screen.type") {
      const text = argumentText(args, "text");
      return text
        ? `Jalora wants to enter "${text}" in ${target || "the selected field"} on ${device}.`
        : `Jalora wants to enter text on ${device}.`;
    }
    if (action === "android.screen.scroll" || action === "android.screen.swipe") {
      const direction = argumentText(args, "direction") || "down";
      return `Jalora wants to ${action.endsWith("swipe") ? "swipe" : "scroll"} ${direction} on ${device}.`;
    }
  }

  if (toolName === "queue_device_reply") {
    const reply = argumentText(args, "reply");
    return reply
      ? `Jalora wants to send this reply from your phone: "${reply}". Review it before sending.`
      : "Jalora wants to send a reply from your phone. Review the message before sending.";
  }

  return null;
}
