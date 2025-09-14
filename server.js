const express = require('express');
const csv = require('csv-parser');
const fs = require('fs');
const path = require('path');

const app = express();
const PORT = process.env.PORT || 3000;

// Serve static files from public directory
app.use(express.static('public'));

// Store table data for different sections
let currentStandingsData = { headers: [], data: [] };
let previousStandingsData = { headers: [], data: [] };
let totalPointsData = { headers: [], data: [] };
let comparisonData = { headers: [], data: [] };
let allgameData = { headers: [], data: [] };
let weekData = {};

// Load CSV data function
function loadCSVFile(filename, callback) {
  const csvFilePath = path.join(__dirname, filename);
  
  if (!fs.existsSync(csvFilePath)) {
    console.log(`No ${filename} file found.`);
    callback({ headers: [], data: [] });
    return;
  }



  const results = [];
  
  fs.createReadStream(csvFilePath)
    .pipe(csv())
    .on('data', (data) => results.push(data))
    .on('end', () => {
      const headers = results.length > 0 ? Object.keys(results[0]) : [];
      console.log(`Loaded ${filename}: ${results.length} rows, ${headers.length} columns`);
      callback({ headers, data: results });
    })
    .on('error', (error) => {
      console.error(`Error loading ${filename}:`, error.message);
      callback({ headers: [], data: [] });
    });
}

// Load all CSV files on server start
function loadAllCSVData() {
  // Load current standings from current-standings.csv only
  loadCSVFile('current-standings.csv', (data) => {
    currentStandingsData = data;
    if (data.headers.length > 0) {
      console.log('Loaded current-standings.csv successfully');
    } else {
      console.log('current-standings.csv not found or empty');
    }
  });

  loadCSVFile('previous-standings.csv', (data) => {
  previousStandingsData = data;
  if (data.headers.length > 0) {
    console.log('Loaded previous-standings.csv successfully');
  } else {
    console.log('previous-standings.csv not found or empty');
  }
});

  // Load total points from total-points.csv only
  loadCSVFile('total-points.csv', (data) => {
    totalPointsData = data;
    if (data.headers.length > 0) {
      console.log('Loaded total-points.csv successfully');
    } else {
      console.log('total-points.csv not found or empty');
    }
  });

  // Load 2024 comparison from 2024-comparison.csv
  loadCSVFile('2024-comparison.csv', (data) => {
    comparisonData = data;
    if (data.headers.length > 0) {
      console.log('Loaded 2024-comparison.csv successfully');
    } else {
      console.log('2024-comparison.csv not found or empty');
    }
  });

  // Load all games from all-games.csv
  loadCSVFile('all-games.csv', (data) => {
    allgameData = data;
    if (data.headers.length > 0) {
      console.log('Loaded all-games.csv successfully');
    } else {
      console.log('all-games.csv not found or empty');
    }
  });

  // Load week data (weeks 1-15)
  for (let week = 1; week <= 15; week++) {
    loadCSVFile(`week-${week}.csv`, (data) => {
      weekData[week] = data;
      if (data.headers.length > 0) {
        console.log(`Loaded week-${week}.csv successfully`);
      }
    });
  }
}

// Routes
app.get('/', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Get current standings data
app.get('/api/current-standings', (req, res) => {
  res.json(currentStandingsData);
});

//Get previous standings data
app.get('/api/previous-standings', (req, res) => {
  res.json(previousStandingsData);
});

// Get total points data
app.get('/api/total-points', (req, res) => {
  res.json(totalPointsData);
});

// Get 2024 comparison data
app.get('/api/2024-comparison', (req, res) => {
  res.json(comparisonData);
});

// Get all games data
app.get('/api/all-games', (req, res) => {
  res.json(allgameData);
});

// Get week data
app.get('/api/week/:weekNumber', (req, res) => {
  const weekNumber = parseInt(req.params.weekNumber);
  if (weekNumber >= 1 && weekNumber <= 15) {
    res.json(weekData[weekNumber] || { headers: [], data: [] });
  } else {
    res.status(400).json({ error: 'Invalid week number' });
  }
});

// Load CSV data when server starts
loadAllCSVData();

app.listen(PORT, () => {
  console.log(`Server running on http://localhost:${PORT}`);
  console.log('Required CSV files:');
  console.log('- current-standings.csv (for Current Standings page)');
  console.log('- previous-standings.csv (for Previous Standings page)');
  console.log('- total-points.csv (for Total Points page)');
  console.log('- 2024-comparison.csv (for 2024 Standing Comparison page)');
  console.log('- week-X.csv (for individual week pages, X = 1-15)');
  console.log('No fallback files will be used.');
});