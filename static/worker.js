self.onmessage = function(e) {
    importScripts('https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js');

    const file = e.data.file;
    const version = e.data.version;
    const reader = new FileReader();

    reader.onload = function(e) {
        try {
            const data = new Uint8Array(e.target.result);
            const workbook = XLSX.read(data, { type: 'array' });
            const sheetNames = workbook.SheetNames;
            const ticketList = [];
            const now = new Date().toISOString().split('T')[0].replace(/-/g, '/');

            sheetNames.forEach(sheetName => {
                const worksheet = workbook.Sheets[sheetName];
                const sheetType = XLSX.utils.sheet_to_json(worksheet, { range: 'B2:B2', header: ['Value'] })[0]?.Value;
                const sheetId = XLSX.utils.sheet_to_json(worksheet, { range: 'B7:B7', header: ['Value'] })[0]?.Value;

                if (sheetType === '項目定義書_画面') {
                    if (String(sheetId).includes('999')) {
                        ticketList.push(['カスタマイズ', '新規', '通常', 'EDISON HCM', sheetName, 'dinhcuong@e-mall.co.jp', version, '']);
                    } else {
                        ticketList.push(['カスタマイズ', '新規', '通常', 'EDISON HCM', `${sheetId} ${sheetName}`, 'dinhcuong@e-mall.co.jp', version, '']);
                    }
                } else if (sheetType === '項目定義書_帳票') {
                    if (String(sheetId).includes('999')) {
                        ticketList.push(['カスタマイズ', '新規', '通常', 'EDISON HCM', `（帳票）${sheetName}`, 'dinhcuong@e-mall.co.jp', version, '']);
                    } else {
                        ticketList.push(['カスタマイズ', '新規', '通常', 'EDISON HCM', `${sheetId}（帳票）${sheetName}`, 'dinhcuong@e-mall.co.jp', version, '']);
                    }
                }
            });

            ticketList.push(['カスタマイズ', '新規', '通常', 'EDISON HCM', 'テストエビデンス', 'dinhcuong@e-mall.co.jp', version, '']);

            self.postMessage({ success: true, ticketList: ticketList });
        } catch (error) {
            self.postMessage({ success: false, error: error.message });
        }
    };

    reader.readAsArrayBuffer(file);
};