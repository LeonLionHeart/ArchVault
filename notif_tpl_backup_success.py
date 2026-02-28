DEFAULT_SUCCESS_HTML = """<div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; background-color: #ffffff;">
    <div style="background-color: #0b0e1a; padding: 24px; text-align: center; border-bottom: 3px solid #10b981;">
        <h1 style="margin: 0; font-size: 26px; font-weight: 800; letter-spacing: 1px;">
            <span style="color: #ffffff;">Arch</span><span style="color: #0ea5e9;">Vault</span>
        </h1>
    </div>
    <div style="padding: 32px; color: #1e293b;">
        <h2 style="color: #10b981; margin-top: 0; font-size: 20px;">✅ Backup Successful</h2>
        <p style="font-size: 14px; line-height: 1.6; color: #475569;">Your scheduled automated backup has completed successfully and is securely stored.</p>
        
        <table style="width: 100%; border-collapse: collapse; margin-top: 24px; font-size: 14px;">
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #f1f5f9; font-weight: 600; color: #64748b; width: 35%;">Target Profile:</td>
                <td style="padding: 12px; border-bottom: 1px solid #f1f5f9; font-weight: bold; color: #0f172a;">{{target}}</td>
            </tr>
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #f1f5f9; font-weight: 600; color: #64748b;">Operation Type:</td>
                <td style="padding: 12px; border-bottom: 1px solid #f1f5f9; color: #0f172a;">{{job_type}}</td>
            </tr>
            <tr>
                <td style="padding: 12px; font-weight: 600; color: #64748b;">Completion Time:</td>
                <td style="padding: 12px; color: #0f172a;">{{time}}</td>
            </tr>
        </table>
    </div>
    <div style="background-color: #f8fafc; padding: 16px; text-align: center; color: #94a3b8; font-size: 12px; border-top: 1px solid #e2e8f0;">
        ArchVault Professional Backup Suite<br>Automated System Notification
    </div>
</div>"""
