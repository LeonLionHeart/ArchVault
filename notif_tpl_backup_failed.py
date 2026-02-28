DEFAULT_FAILED_HTML = """<div style="font-family: 'Segoe UI', Arial, sans-serif; max-width: 600px; margin: 0 auto; border: 1px solid #e2e8f0; border-radius: 12px; overflow: hidden; background-color: #ffffff;">
    <div style="background-color: #0b0e1a; padding: 24px; text-align: center; border-bottom: 3px solid #ef4444;">
        <h1 style="margin: 0; font-size: 26px; font-weight: 800; letter-spacing: 1px;">
            <span style="color: #ffffff;">Arch</span><span style="color: #0ea5e9;">Vault</span>
        </h1>
    </div>
    <div style="padding: 32px; color: #1e293b;">
        <h2 style="color: #ef4444; margin-top: 0; font-size: 20px;">❌ Backup Failed</h2>
        <p style="font-size: 14px; line-height: 1.6; color: #475569;">Your scheduled automated backup encountered a critical error and could not complete.</p>
        
        <table style="width: 100%; border-collapse: collapse; margin-top: 24px; font-size: 14px;">
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #f1f5f9; font-weight: 600; color: #64748b; width: 35%;">Target Profile:</td>
                <td style="padding: 12px; border-bottom: 1px solid #f1f5f9; font-weight: bold; color: #0f172a;">{{target}}</td>
            </tr>
            <tr>
                <td style="padding: 12px; border-bottom: 1px solid #f1f5f9; font-weight: 600; color: #64748b;">Failure Time:</td>
                <td style="padding: 12px; border-bottom: 1px solid #f1f5f9; color: #0f172a;">{{time}}</td>
            </tr>
        </table>

        <div style="margin-top: 24px; background-color: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 16px;">
            <p style="margin: 0 0 8px 0; font-weight: 600; color: #991b1b; font-size: 12px; text-transform: uppercase;">Diagnostic Log</p>
            <pre style="margin: 0; font-family: monospace; font-size: 11px; color: #7f1d1d; white-space: pre-wrap;">{{log_tail}}</pre>
        </div>
    </div>
    <div style="background-color: #f8fafc; padding: 16px; text-align: center; color: #94a3b8; font-size: 12px; border-top: 1px solid #e2e8f0;">
        ArchVault Professional Backup Suite<br>Please log in to the server to investigate this error.
    </div>
</div>"""
