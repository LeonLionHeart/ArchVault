DEFAULT_ALERT_HTML = """<div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; background-color: #ffffff;">
    <div style="background-color: #0b0e1a; padding: 24px; text-align: center; border-bottom: 3px solid #f59e0b;">
        <h1 style="margin: 0; font-size: 26px; font-weight: 800; letter-spacing: 1px;">
            <span style="color: #ffffff;">Arch</span><span style="color: #0ea5e9;">Vault</span>
        </h1>
    </div>
    <div style="padding: 32px; color: #1e293b;">
        <h2 style="color: #f59e0b; margin-top: 0; font-size: 20px;">⚠️ System Alert</h2>
        <p style="font-size: 14px; line-height: 1.6; color: #475569;">ArchVault has generated an automated system warning that requires your attention.</p>
        
        <div style="margin-top: 24px; background-color: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; padding: 16px;">
            <p style="margin: 0; font-family: monospace; font-size: 13px; color: #92400e;">{{alert_message}}</p>
        </div>
    </div>
    <div style="background-color: #f8fafc; padding: 16px; text-align: center; color: #94a3b8; font-size: 12px; border-top: 1px solid #e2e8f0;">
        ArchVault Professional Backup Suite
    </div>
</div>"""
