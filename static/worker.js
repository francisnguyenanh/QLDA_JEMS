self.onmessage = function(e) {
    importScripts('https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js');

    const { file, version } = e.data;
    const reader = new FileReader();

    reader.onload = function(e) {
        try {
            const data = new Uint8Array(e.target.result);
            const workbook = XLSX.read(data, { type: 'array' });
            const ticketList = [];

            // Mapping cho các loại sheet
            const sheetTypeConfig = {
                '項目定義書_画面': '',
                '項目定義書_帳票': '（帳票）',
                '項目定義書_CSV': '（CSV）'
            };

            // Helper function tạo ticket
            const createTicket = (sheetId, sheetName, prefix = '') => {
                const title = String(sheetId).includes('999') 
                    ? `${prefix}${sheetName}`
                    : `${sheetId}\u3000${prefix}${sheetName}`;
                return ['カスタマイズ', '新規', '通常', 'EDISON HCM', title, 'dinhcuong@e-mall.co.jp', version, ''];
            };

            workbook.SheetNames.forEach(sheetName => {
                const worksheet = workbook.Sheets[sheetName];
                const sheetType = XLSX.utils.sheet_to_json(worksheet, { range: 'B2:B2', header: ['Value'] })[0]?.Value;
                const sheetId = XLSX.utils.sheet_to_json(worksheet, { range: 'B7:B7', header: ['Value'] })[0]?.Value;

                if (sheetTypeConfig.hasOwnProperty(sheetType)) {
                    const prefix = sheetTypeConfig[sheetType];
                    ticketList.push(createTicket(sheetId, sheetName, prefix));
                }
            });

            self.postMessage({ success: true, ticketList });
        } catch (error) {
            self.postMessage({ success: false, error: error.message });
        }
    };

    reader.readAsArrayBuffer(file);
};